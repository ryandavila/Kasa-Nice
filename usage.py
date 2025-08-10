import asyncio
import calendar

import plotly.graph_objects as go
from kasa.iot import IotDevice
from nicegui import ui

from logging_config import get_logger

logger = get_logger(__name__)


def draw_kasa_plots(devices: dict[str, IotDevice]):
    for type in ["Bulb", "Plug", "Dimmer", "LightStrip"]:
        ui.label(text=f"{type}s").classes("h1 text-primary")
        for device in devices.values():
            if device.device_type.name == type and device.has_emeter:
                usage = asyncio.run(handle_metering(device.alias, devices))
                if usage is not None:
                    with ui.expansion(text=device.alias):
                        with ui.splitter() as splitter:
                            with splitter.before:
                                ui.label("Daily")

                                daily = go.Figure(
                                    go.Scatter(
                                        x=list(usage[0].keys()),
                                        y=list(usage[0].values()),
                                    )
                                )
                                daily.update_layout(
                                    margin={"l": 0, "r": 0, "t": 0, "b": 0}
                                )
                                daily.update_xaxes(
                                    tickformat="%b",
                                    tickvals=list(usage[0].keys()),
                                )
                                ui.plotly(daily).classes("w-96 h-48")
                            with splitter.after:
                                ui.label("Monthly")
                                plotx, ploty = (
                                    list(usage[1].keys()),
                                    list(usage[1].values()),
                                )
                                monthly = go.Figure(go.Scatter(x=plotx, y=ploty))
                                monthly.update_layout(
                                    margin={"l": 0, "r": 0, "t": 0, "b": 0}
                                )
                                monthly.update_xaxes(
                                    tickformat="%b",
                                    tickvals=plotx,
                                    ticktext=[
                                        calendar.month_abbr[i - 1]
                                        for i in plotx
                                        if i != 0
                                    ],
                                )
                                ui.plotly(monthly).classes("w-96 h-48")


async def handle_metering(dev_alias, devices: dict[str, IotDevice]):
    for device in devices.values():
        if device.alias == dev_alias and device.has_emeter:
            try:
                daily_usage_dict = await device.get_emeter_daily()
                monthly_usage_dict = await device.get_emeter_monthly()
                return [daily_usage_dict, monthly_usage_dict]
            except Exception as e:
                logger.error(f"Error getting emeter data for {device.alias}: {e}")
