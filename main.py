import asyncio
import os
from colorsys import hsv_to_rgb, rgb_to_hsv

from kasa import Discover, Module
from nicegui import app as nicegui_app
from nicegui import ui  # ,Tailwind

from logging_config import get_logger, setup_logging

logger = get_logger(__name__)

# https://stackoverflow.com/questions/76726671/im-populating-some-cards-using-for-loop-in-nicegui-each-has-a-label-and-a-butto
# https://github.com/python-kasa/python-kasa/issues/345 #pertaining to KL125 bulbs

nicegui_app.add_static_files("/static", "static")
devices = {}


async def discover_devices():
    """Discover and initialize Kasa devices on startup."""
    global devices
    logger.info("Starting device discovery")
    discovered = await Discover.discover()
    for addr, device in discovered.items():
        try:
            await device.update()
        except Exception as e:
            logger.error(f"Error updating device {addr}: {e}")
    devices.update(discovered)
    logger.info(f"Discovered {len(devices)} devices")


# Register startup handler
nicegui_app.on_startup(discover_devices)


def hex_to_hsv(hex_color):
    hex_color = hex_color.lstrip("#")
    rgb = tuple(int(hex_color[i : i + 2], 16) for i in (0, 2, 4))
    (r, g, b) = rgb
    (h, s, v) = rgb_to_hsv(r / 255, g / 255, b / 255)
    (h, s, v) = (int(h * 360), int(s * 100), int(v * 100))
    return h, s, v


def hsv_to_hex(hsv):
    (h, s, v) = hsv
    (r, g, b) = hsv_to_rgb((h / 360), (s / 100), (v / 100))
    (r, g, b) = (int(r * 255), int(g * 255), int(b * 255))
    (r, g, b) = (hex(r), hex(g), hex(b))
    (r, g, b) = (r[2:], g[2:], b[2:])
    hex_color = f"#{r}{g}{b}"
    if hex_color == "#ffffff":
        hex_color = "#4acbd6"
    return hex_color


def set_device_icon(device_type):
    icons = {
        "Bulb": "lightbulb",
        "Plug": "outlet",
        "Dimmer": "tungsten",
        "Strip": "electrical_services",
        "LightStrip": "linear_scale",
    }
    icon_name = icons.get(device_type, "help")
    return ui.icon(icon_name, color="primary").classes("text-5xl")


async def handle_color_picker(dev_alias, hex_color, button, switch):
    hsv = hex_to_hsv(hex_color)
    if hex_color == "#ffffff":
        hex_color = "#4acbd6"
    button.style(f"background-color:{hex_color}!important")
    switch.value = True
    for device in devices.values():
        if device.alias == dev_alias:
            await set_bulb_hsv(device, hsv)


async def set_bulb_hsv(device, hsv=(0, 0, 100)):
    await device.update()
    light = device.modules[Module.Light]
    try:
        (h, s, v) = hsv
        await light.set_hsv(h, s, v)
        await device.update()
    except Exception as e:
        logger.error(f"Error setting bulb HSV for {device.alias}: {e}")


async def handle_brightness(dev_alias, brightness_value, switch):
    for device in devices.values():
        if device.alias == dev_alias:
            light = device.modules[Module.Light]
            await light.set_brightness(brightness_value)
            # await device.set_brightness(brightness_value)
    switch.value = True


async def handle_lightstrip(dev_alias, effect, switch):
    for device in devices.values():
        if device.alias == dev_alias:
            await device.set_effect(effect)
            ui.notify(
                message=f"{device.alias} set to {effect}", position="top", color="info"
            )
            switch.value = True


async def handle_discovery(ip_address):
    discovery_result.clear()
    div_element.clear()
    discovery_result.text = f"Discovering: {ip_address}"
    with div_element:
        spinner = ui.spinner("dots", size="lg", color="primary")
        spinner.visible = True
    response = await Discover.discover(target=ip_address)
    spinner.visible = False
    discovery_result.text = ""
    with div_element:
        for device in response.values():
            with ui.row() as row:
                set_device_icon(device.device_type.name)
                ui.switch(
                    text=device.alias,
                    value=device.is_on,
                    on_change=lambda v, dev=device: dev.turn_on()
                    if v.value
                    else dev.turn_off(),
                )

                def create_pin_handler(current_row, button):
                    def handler():
                        current_row.move(pinned_devices)
                        discovery_result.set_text("")
                        button.delete()

                    return handler

                pin_button = ui.icon("push_pin").classes("text-2xl text-primary")
                pin_button.on("click", create_pin_handler(row, pin_button))


async def kasa_device_on_off(dev_alias, boolean):
    for device in devices.values():
        if device.alias == dev_alias:
            if boolean:
                ui.notify(f"{device.alias} ON", color="positive")
                logger.info(f"Turning ON {device.alias}")
                await device.turn_on()
            else:
                ui.notify(f"{device.alias} OFF", color="negative")
                logger.info(f"Turning OFF {device.alias}")
                await device.turn_off()


async def kasa_child_on_off(dev_alias, boolean):
    for device in devices.values():
        if len(device.children) >= 2:
            for plug in device.children:
                if plug.alias == dev_alias:
                    if boolean:
                        ui.notify(f"{plug.alias} ON", color="positive")
                        logger.info(f"Turning ON {plug.alias}")
                        await plug.turn_on()
                    else:
                        ui.notify(f"{plug.alias} OFF", color="negative")
                        logger.info(f"Turning OFF {plug.alias}")
                        await plug.turn_off()


@ui.page('/')
def index():
    """Main page for Kasa-Nice controller."""
    ui.colors(primary="#4acbd6")
    dark = ui.dark_mode(True)

    with ui.left_drawer(value=False) as drawer:
        with ui.column():
            with ui.row():
                ui.button("Dark", on_click=dark.enable)
                ui.button("Light", on_click=dark.disable)
            ip_checkbox = ui.checkbox("Show device IP address")
            md_checkbox = ui.checkbox("Show device model")
            ui.label("Please ensure that none of your devices share the same alias.")
            ui.separator()
            ui.separator()
            ui.button(icon="close", on_click=drawer.toggle)

    with ui.tabs().classes("w-full") as tabs:
        one = ui.tab("Devices")
        two = ui.tab("Discovery")
        three = ui.tab("Usage")

    with ui.tab_panels(tabs, value=one).classes("w-full"):
        with ui.tab_panel(one):
            for type in ["Bulb", "Plug", "Dimmer", "Strip", "LightStrip"]:
                ui.label(text=f"{type}s").classes("text-weight-bold")
                for device in devices.values():
                    if device.device_type.name == type:
                        with ui.row().classes("w-full"):
                            set_device_icon(device.device_type.name)
                            device_ip = ui.label(text=device.host).bind_visibility_from(
                                ip_checkbox, "value"
                            )
                            modelname = ui.label(text=device.model).bind_visibility_from(
                                md_checkbox, "value"
                            )

                            if len(device.children) >= 2:
                                for child in device.children:
                                    ui.switch(
                                        text=child.alias,
                                        value=child.is_on,
                                        on_change=lambda v: kasa_child_on_off(
                                            v.sender.text, v.value
                                        ),
                                    )
                                continue

                            switch = ui.switch(
                                text=device.alias,
                                value=device.is_on,
                                on_change=lambda v: kasa_device_on_off(
                                    v.sender.text, v.value
                                ),
                            ).classes("w-[calc(20%-2px)]")
                            if device.device_type.name in [
                                "Bulb",
                                "Strip",
                                "Dimmer",
                                "LightStrip",
                            ]:
                                dev_sys_info = device.sys_info
                                if dev_sys_info.get("is_color", 0):
                                    light = device.modules[Module.Light]
                                    hex_color = hsv_to_hex(light.hsv)
                                    with ui.button(icon="colorize") as button:
                                        button.style(
                                            f"background-color:{hex_color}!important"
                                        )
                                        ui.color_picker(
                                            on_pick=lambda e,
                                            dev_alias=switch.text,
                                            button=button,
                                            switch=switch: handle_color_picker(
                                                dev_alias, e.color, button, switch
                                            )
                                        )
                                if dev_sys_info.get("is_dimmable", 0):
                                    light = device.modules[Module.Light]
                                    slider = ui.slider(
                                        min=0,
                                        max=100,
                                        value=light.brightness,
                                        on_change=lambda s,
                                        dev_alias=switch.text,
                                        switch=switch: handle_brightness(
                                            dev_alias, s.value, switch
                                        ),
                                    ).classes("w-[calc(50%-2px)]")
                                # if device._has_effects:
                                #   select1 = ui.select([effect for effect in device.effect_list], \
                                #                       on_change=lambda e, dev_alias=switch.text, switch=switch: handle_lightstrip(dev_alias, e.value, switch))
                ui.separator()

        with ui.tab_panel(two):
            ui.input(label="LAN IP address", value="255.255.255.255").on(
                "keydown.enter", lambda t: handle_discovery(t.sender.value)
            )
            discovery_result = ui.label()
            div_element = ui.element()
            ui.separator()
            pinned_devices = ui.element()

        with ui.tab_panel(three):
            # draw_kasa_plots(devices)
            ui.separator()

    with ui.header() as header:
        header.style("background-color:white!important")
        ui.button(icon="menu", on_click=drawer.toggle)
        ui.image("/static/images/logo.png").classes("w-24")

    with ui.footer(value=False) as footer:
        with ui.element() as tooltip:
            ui.label(
                "Discovery is useful when a local device is not appearing in your device list."
            )
            ui.label(
                "If you know the smart device's IP address, type it to directly look for that device."
            )
            ui.label(
                "Otherwise, use '255.255.255.255' to find all discoverable devices in your local network."
            )

    with ui.page_sticky(position="bottom-right", x_offset=20, y_offset=20):
        ui.button(on_click=footer.toggle).props("fab icon=contact_support")


def main():
    setup_logging()
    logger.info("Starting Kasa-Nice application")

    host = os.getenv("KASA_HOST", "127.0.0.1")
    port = int(os.getenv("KASA_PORT", "8080"))

    logger.info(f"Running on {host}:{port}")
    ui.run(host=host, port=port, title="Kasa-Nice Smart Home Controller")


if __name__ in {"__main__", "__mp_main__"}:
    main()
