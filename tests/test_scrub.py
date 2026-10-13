"""D1: file paths, user names and machine names never leave the .kra."""

import pytest

from krita_provenance.scrub import Identity, file_name, scrub, scrub_text

KABLE = Identity(
    user_names=("kable", "Kable Morrill"),  # shorter first on purpose: longest must win
    home="C:\\Users\\Kable Morrill",
    host_names=("studio-pc.local", "studio-pc"),
)
NOBODY = Identity()


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("C:\\Users\\Kable Morrill\\Pictures\\harbour.png", "harbour.png"),
        ("c:/users/kable morrill/pictures/harbour.png", "harbour.png"),
        ("/home/kable/art/background.png", "background.png"),
        ("~/art/background.png", "background.png"),
        ("\\\\studio-pc\\share\\refs\\pose.jpg", "pose.jpg"),
        ("/Users/kable/models/", "models"),
        ("D:\\Backups\\Kable Morrill\\x.png", "x.png"),
    ],
)
def test_a_path_becomes_its_file_name(text: str, expected: str):
    assert scrub_text(text, KABLE) == expected
    assert scrub_text(text, NOBODY) == expected  # paths are cut even when the user is unknown


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Placed C:\\Users\\kable\\refs\\pose.jpg on layer Pose", "Placed pose.jpg on layer Pose"),
        ("Opened /home/kable/art/sketch.png.", "Opened sketch.png."),
        ("See ~/art/sketch.png, then /tmp/out/x.png", "See sketch.png, then x.png"),
        ("Model at \\\\studio-pc\\models\\v1.safetensors", "Model at v1.safetensors"),
        ("Model file:C:/models/v1.safetensors", "Model file:v1.safetensors"),
    ],
)
def test_a_path_inside_prose_becomes_its_file_name(text: str, expected: str):
    assert scrub_text(text, NOBODY) == expected


@pytest.mark.parametrize(
    "text",
    [
        "sd15/dreamshaper_8.safetensors",
        "SDXL\\juggernaut.safetensors",
        "https://example.com/kable-portfolio/harbour.png",
        "http://cv.iptc.org/newscodes/digitalsourcetype/compositeWithTrainedAlgorithmicMedia",
        "black/white, 1/2 strength and/or w/ friends",
        "urn:c2pa:0f6e1d2c-3b4a-4596-8778-99aabbccddee",
        "Inpainted region on layer Sky",
    ],
)
def test_relative_names_urls_and_prose_are_left_alone(text: str):
    assert scrub_text(text, NOBODY) == text


def test_user_and_machine_names_are_replaced_wherever_they_appear():
    assert scrub_text("Signed by kable on studio-pc", KABLE) == "Signed by [user] on [machine]"
    assert (
        scrub_text("KABLE's layer, host STUDIO-PC.local", KABLE) == "[user]'s layer, host [machine]"
    )
    assert scrub_text("Kable Morrill (kable)", KABLE) == "[user] ([user])"
    assert scrub_text("kable-portrait.png", KABLE) == "[user]-portrait.png"


def test_name_inside_a_word_is_not_replaced():
    assert scrub_text("kabletown skablem", KABLE) == "kabletown skablem"


def test_the_home_directory_goes_even_with_spaces_or_other_separators():
    assert scrub_text("C:\\Users\\Kable Morrill\\Pictures\\x.png", KABLE) == "x.png"
    assert scrub_text("saved in c:/users/kable morrill/pictures", KABLE) == "saved in pictures"
    linux = Identity(home="/home/kable", user_names=("kable",))
    assert scrub_text("/home/kable/art/x.png", linux) == "x.png"
    assert scrub_text("home is /home/kable", linux) == "home is ~"  # a bare home becomes ~


def test_short_names_are_not_scrubbed():
    short = Identity(user_names=("al",), host_names=("pc",))
    assert scrub_text("al on pc, really", short) == "al on pc, really"


def test_scrub_walks_nested_structures_and_keeps_types():
    value = {
        "title": "C:\\Users\\kable\\harbour.png",
        "actions": [{"description": "By kable", "when": "2026-11-02T18:04:11Z", "steps": 24}],
        "bounds": (0, 0, 2048, 2048),
        "ok": True,
        "nothing": None,
    }
    assert scrub(value, KABLE) == {
        "title": "harbour.png",
        "actions": [{"description": "By [user]", "when": "2026-11-02T18:04:11Z", "steps": 24}],
        "bounds": (0, 0, 2048, 2048),
        "ok": True,
        "nothing": None,
    }
    assert scrub(b"bytes", KABLE) == b"bytes"


def test_file_name_handles_every_separator_style():
    assert file_name("C:\\a\\b\\c.png") == "c.png"
    assert file_name("/a/b/c.png") == "c.png"
    assert file_name("a/b\\c.png") == "c.png"
    assert file_name("/a/b/") == "b"
    assert file_name("c.png") == "c.png"
    assert file_name("C:\\") == "C:\\"
    assert file_name("/") == "/"


def test_current_identity_describes_this_machine():
    identity = Identity.current()
    assert identity.home is None or identity.home
    assert all(name.strip() for name in identity.user_names + identity.host_names)
    assert len(set(identity.user_names)) == len(identity.user_names)
