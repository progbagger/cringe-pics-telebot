from pathlib import Path

import pytest
from hamcrest import assert_that, contains_exactly, empty, equal_to, has_length
from PIL import Image, ImageDraw

from cringe_pics_telebot.services.media_duplicate_detection import MediaCandidate, find_duplicate_groups


def _make_test_image(path: Path, size: tuple[int, int] = (480, 360)) -> None:
    image = Image.new("RGB", size, "#e8d6b0")
    draw = ImageDraw.Draw(image)
    draw.rectangle((20, 25, 210, 160), fill="#184a70")
    draw.ellipse((250, 60, 430, 240), fill="#cc503e")
    draw.polygon(((60, 300), (220, 190), (350, 330)), fill="#4c956c")
    for x in range(0, size[0], 20):
        draw.line((x, 0, size[0] - x // 2, size[1]), fill="#f4f1de", width=2)
    image.save(path)


@pytest.mark.parametrize(
    ("suffix", "compare_visually"),
    [(".png", True), (".gif", False), (".mp4", False)],
)
def test_exact_media_copies_are_grouped(
    tmp_path: Path,
    suffix: str,
    compare_visually: bool,
) -> None:
    first = tmp_path / f"first{suffix}"
    second = tmp_path / f"second{suffix}"
    if suffix == ".png":
        _make_test_image(first)
    else:
        first.write_bytes(b"exact media payload")
    second.write_bytes(first.read_bytes())

    scan = find_duplicate_groups(
        (
            MediaCandidate(first, compare_visually),
            MediaCandidate(second, compare_visually),
        )
    )

    assert_that(scan.groups, has_length(1))
    assert_that(scan.groups[0].kept.path, equal_to(first))
    assert_that([duplicate.path for duplicate in scan.groups[0].duplicates], contains_exactly(second))


@pytest.mark.parametrize("variant", ["resize", "crop"])
def test_visual_variants_keep_the_full_resolution_source(tmp_path: Path, variant: str) -> None:
    source = tmp_path / "source.png"
    transformed = tmp_path / "transformed.jpg"
    _make_test_image(source)
    with Image.open(source) as image:
        if variant == "resize":
            image.resize((240, 180), Image.Resampling.LANCZOS).save(transformed, quality=58)
        else:
            image.crop((34, 14, 466, 331)).resize((480, 360)).save(transformed, quality=90)

    scan = find_duplicate_groups((MediaCandidate(source, True), MediaCandidate(transformed, True)))

    assert_that(scan.groups, has_length(1))
    assert_that(scan.groups[0].kept.path, equal_to(source))
    assert_that([duplicate.path for duplicate in scan.groups[0].duplicates], contains_exactly(transformed))


def test_lossless_image_wins_at_the_same_resolution(tmp_path: Path) -> None:
    source = tmp_path / "source.png"
    compressed = tmp_path / "compressed.jpg"
    _make_test_image(source)
    with Image.open(source) as image:
        image.save(compressed, quality=45)

    scan = find_duplicate_groups((MediaCandidate(compressed, True), MediaCandidate(source, True)))

    assert_that(scan.groups, has_length(1))
    assert_that(scan.groups[0].kept.path, equal_to(source))


def test_unrelated_images_are_not_grouped(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _make_test_image(first)
    Image.new("RGB", (480, 360), "#202020").save(second)

    scan = find_duplicate_groups((MediaCandidate(first, True), MediaCandidate(second, True)))

    assert_that(scan.groups, empty())
    assert_that(scan.errors, empty())


def test_decode_errors_still_allow_exact_comparison(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    first.write_bytes(b"not an image")
    second.write_bytes(first.read_bytes())

    scan = find_duplicate_groups((MediaCandidate(first, True), MediaCandidate(second, True)))

    assert_that(scan.groups, has_length(1))
    assert_that(scan.errors, has_length(2))


def test_image_over_pixel_limit_is_not_visually_compared(tmp_path: Path) -> None:
    first = tmp_path / "first.png"
    second = tmp_path / "second.png"
    _make_test_image(first, size=(20, 20))
    Image.new("RGB", (20, 20), "#202020").save(second)

    scan = find_duplicate_groups(
        (MediaCandidate(first, True), MediaCandidate(second, True)),
        max_image_pixels=399,
    )

    assert_that(scan.groups, empty())
    assert_that(scan.errors, has_length(2))
