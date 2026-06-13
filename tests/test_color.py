from api.kasa_service import hex_to_hsv, hsv_to_hex


def test_hex_to_hsv_primary_colors():
    assert hex_to_hsv("#ff0000") == (0, 100, 100)  # red
    assert hex_to_hsv("#00ff00") == (120, 100, 100)  # green
    assert hex_to_hsv("#0000ff") == (240, 100, 100)  # blue


def test_hex_to_hsv_accepts_no_hash():
    assert hex_to_hsv("ff0000") == hex_to_hsv("#ff0000")


def test_hex_to_hsv_black_and_white():
    assert hex_to_hsv("#000000") == (0, 0, 0)
    assert hex_to_hsv("#ffffff") == (0, 0, 100)


def test_hsv_to_hex_primary_colors():
    assert hsv_to_hex((0, 100, 100)) == "#ff0000"
    assert hsv_to_hex((120, 100, 100)) == "#00ff00"
    assert hsv_to_hex((240, 100, 100)) == "#0000ff"


def test_roundtrip_is_stable_for_saturated_colors():
    for hex_color in ("#ff0000", "#00ff00", "#0000ff", "#ffff00", "#00ffff"):
        assert hsv_to_hex(hex_to_hsv(hex_color)) == hex_color
