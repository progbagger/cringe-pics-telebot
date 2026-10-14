import hashlib
import logging
import math
from collections import defaultdict
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps, UnidentifiedImageError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MediaCandidate:
    path: Path
    compare_visually: bool


@dataclass(frozen=True, slots=True)
class Sensitivity:
    hash_distance: int
    difference_hash_distance: int
    pixel_similarity: float


SENSITIVITIES = {
    "strict": Sensitivity(6, 32, 0.94),
    "balanced": Sensitivity(10, 80, 0.88),
    "loose": Sensitivity(14, 96, 0.80),
}


@dataclass(frozen=True, slots=True)
class ImageVariant:
    coverage: float
    perceptual_hash: int
    difference_hash: int
    gray: np.ndarray
    rgb: np.ndarray


@dataclass(frozen=True, slots=True)
class ImageSignature:
    width: int
    height: int
    format_name: str
    sharpness: float
    preview: np.ndarray
    variants: tuple[ImageVariant, ...]


@dataclass(frozen=True, slots=True)
class MediaFile:
    path: Path
    size: int
    digest: str
    image: ImageSignature | None


@dataclass(frozen=True, slots=True)
class ImageMatch:
    left_index: int
    right_index: int
    left_coverage: float
    right_coverage: float
    similarity: float


@dataclass(frozen=True, slots=True)
class DuplicateGroup:
    kept: MediaFile
    duplicates: tuple[MediaFile, ...]


@dataclass(frozen=True, slots=True)
class ScanResult:
    files: tuple[MediaFile, ...]
    groups: tuple[DuplicateGroup, ...]
    errors: tuple[str, ...]


class UnionFind:
    def __init__(self, size: int) -> None:
        self.parents = list(range(size))
        self.ranks = [0] * size

    def find(self, item: int) -> int:
        # Compress traversed paths so repeated group lookups stay nearly constant-time.
        while self.parents[item] != item:
            self.parents[item] = self.parents[self.parents[item]]
            item = self.parents[item]

        return item

    def union(self, left: int, right: int) -> bool:
        left_root = self.find(left)
        right_root = self.find(right)
        if left_root == right_root:
            return False

        if self.ranks[left_root] < self.ranks[right_root]:
            left_root, right_root = right_root, left_root

        self.parents[right_root] = left_root
        if self.ranks[left_root] == self.ranks[right_root]:
            self.ranks[left_root] += 1

        return True


@dataclass(slots=True)
class _BKNode:
    value: int
    payloads: list[tuple[int, int]]
    children: dict[int, _BKNode]


class HammingBKTree:
    def __init__(self) -> None:
        self.root: _BKNode | None = None

    def add(self, value: int, payload: tuple[int, int]) -> None:
        if self.root is None:
            self.root = _BKNode(value, [payload], {})
            return

        node = self.root
        while True:
            distance = hamming_distance(value, node.value)
            if distance == 0:
                node.payloads.append(payload)
                return

            child = node.children.get(distance)
            if child is None:
                node.children[distance] = _BKNode(value, [payload], {})
                return
            node = child

    def search(self, value: int, radius: int) -> Iterator[tuple[int, int, int]]:
        if self.root is None:
            return

        pending = [self.root]
        while pending:
            node = pending.pop()
            distance = hamming_distance(value, node.value)
            if distance <= radius:
                for file_index, variant_index in node.payloads:
                    yield distance, file_index, variant_index

            # Only edges inside this interval can lead to a value within the radius.
            lower = distance - radius
            upper = distance + radius
            pending.extend(child for edge, child in node.children.items() if lower <= edge <= upper)


def hamming_distance(left: int, right: int) -> int:
    return (left ^ right).bit_count()


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _dct_matrix(size: int) -> np.ndarray:
    positions = np.arange(size, dtype=np.float32)
    frequencies = positions[:, None]
    matrix = np.cos(math.pi * (positions + 0.5) * frequencies / size)
    matrix[0] *= math.sqrt(1 / size)
    matrix[1:] *= math.sqrt(2 / size)
    return matrix.astype(np.float32)


DCT_32 = _dct_matrix(32)


def _perceptual_hash(image: Image.Image) -> int:
    resized = image.convert("L").resize((32, 32), Image.Resampling.LANCZOS)
    pixels = np.asarray(resized, dtype=np.float32)

    # Low-frequency DCT coefficients retain composition while discarding encoding noise.
    coefficients = DCT_32 @ pixels @ DCT_32.T
    low_frequencies = coefficients[:8, :8].copy()
    values_without_dc = low_frequencies.ravel()[1:]
    threshold = float(np.median(values_without_dc))
    bits = low_frequencies.ravel() > threshold
    bits[0] = False

    result = 0
    for bit in bits:
        result = (result << 1) | int(bit)

    return result


def _difference_hash(image: Image.Image) -> int:
    resized = image.convert("L").resize((17, 16), Image.Resampling.LANCZOS)
    pixels = np.asarray(resized, dtype=np.int16)
    bits = pixels[:, 1:] > pixels[:, :-1]

    result = 0
    for bit in bits.ravel():
        result = (result << 1) | int(bit)

    return result


def _prepare_rgb(image: Image.Image) -> Image.Image:
    image = ImageOps.exif_transpose(image)
    if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
        rgba = image.convert("RGBA")
        background = Image.new("RGBA", rgba.size, "white")
        return Image.alpha_composite(background, rgba).convert("RGB")

    return image.convert("RGB")


def _crop_boxes(crop_tolerance: float) -> list[tuple[float, float, float, float]]:
    boxes = [(0.0, 0.0, 1.0, 1.0)]
    if crop_tolerance <= 0:
        return boxes

    fractions = sorted({1.0 - crop_tolerance / 2, 1.0 - crop_tolerance}, reverse=True)
    for fraction in fractions:
        # Cover edge crops, centered crops, and corner crops at each supported extent.
        offsets = (0.0, (1.0 - fraction) / 2, 1.0 - fraction)
        boxes.extend((offset, 0.0, offset + fraction, 1.0) for offset in offsets)
        boxes.extend((0.0, offset, 1.0, offset + fraction) for offset in offsets)
        boxes.extend(
            [
                (0.0, 0.0, fraction, fraction),
                (1.0 - fraction, 0.0, 1.0, fraction),
                (
                    (1.0 - fraction) / 2,
                    (1.0 - fraction) / 2,
                    (1.0 + fraction) / 2,
                    (1.0 + fraction) / 2,
                ),
                (0.0, 1.0 - fraction, fraction, 1.0),
                (1.0 - fraction, 1.0 - fraction, 1.0, 1.0),
            ]
        )

    return boxes


def _image_variant(
    image: Image.Image,
    box: tuple[float, float, float, float],
) -> ImageVariant:
    width, height = image.size
    pixel_box = (
        round(box[0] * width),
        round(box[1] * height),
        round(box[2] * width),
        round(box[3] * height),
    )
    cropped = image.crop(pixel_box)
    gray = np.asarray(
        cropped.convert("L").resize((64, 64), Image.Resampling.LANCZOS),
        dtype=np.uint8,
    )
    rgb = np.asarray(
        cropped.resize((32, 32), Image.Resampling.LANCZOS),
        dtype=np.uint8,
    )
    coverage = (box[2] - box[0]) * (box[3] - box[1])
    return ImageVariant(
        coverage=coverage,
        perceptual_hash=_perceptual_hash(cropped),
        difference_hash=_difference_hash(cropped),
        gray=gray,
        rgb=rgb,
    )


def _sharpness(image: Image.Image) -> float:
    thumbnail = image.convert("L")
    thumbnail.thumbnail((512, 512), Image.Resampling.LANCZOS)
    pixels = np.asarray(thumbnail, dtype=np.float32) / 255.0
    if min(pixels.shape) < 3:
        return 0.0

    horizontal = pixels[:, 1:] - pixels[:, :-1]
    vertical = pixels[1:, :] - pixels[:-1, :]

    return float(np.mean(horizontal**2) + np.mean(vertical**2))


def image_signature(
    path: Path,
    crop_tolerance: float,
    *,
    max_image_pixels: int,
) -> ImageSignature | None:
    with Image.open(path) as opened:
        if opened.width * opened.height > max_image_pixels:
            raise ImagePixelLimitError(opened.width, opened.height, max_image_pixels)

        if getattr(opened, "is_animated", False) and getattr(opened, "n_frames", 1) > 1:
            logger.debug("Skipping visual comparison for animated image: %s", path)
            return None

        format_name = opened.format or path.suffix.removeprefix(".").upper()
        opened.seek(0)
        image = _prepare_rgb(opened)
        width, height = image.size
        preview_image = image.copy()
        preview_image.thumbnail((192, 192), Image.Resampling.LANCZOS)
        preview = np.asarray(preview_image, dtype=np.uint8)
        variants = tuple(_image_variant(image, box) for box in _crop_boxes(crop_tolerance))

        return ImageSignature(
            width,
            height,
            format_name,
            _sharpness(image),
            preview,
            variants,
        )


def _block_ssim(left: np.ndarray, right: np.ndarray) -> float:
    left = left.astype(np.float32) / 255.0
    right = right.astype(np.float32) / 255.0
    return _block_ssim_normalized(left, right)


def _block_ssim_normalized(left: np.ndarray, right: np.ndarray) -> float:
    # Compare local 8x8 statistics so small regional changes remain observable.
    block_size = 8
    left_blocks = left.reshape(8, block_size, 8, block_size).transpose(0, 2, 1, 3)
    right_blocks = right.reshape(8, block_size, 8, block_size).transpose(0, 2, 1, 3)
    left_mean = left_blocks.mean(axis=(2, 3))
    right_mean = right_blocks.mean(axis=(2, 3))
    left_centered = left_blocks - left_mean[:, :, None, None]
    right_centered = right_blocks - right_mean[:, :, None, None]
    left_variance = (left_centered**2).mean(axis=(2, 3))
    right_variance = (right_centered**2).mean(axis=(2, 3))
    covariance = (left_centered * right_centered).mean(axis=(2, 3))
    c1 = 0.01**2
    c2 = 0.03**2
    numerator = (2 * left_mean * right_mean + c1) * (2 * covariance + c2)
    denominator = (left_mean**2 + right_mean**2 + c1) * (left_variance + right_variance + c2)

    return float(np.clip(numerator / denominator, -1, 1).mean())


def _pixel_similarity(left: ImageVariant, right: ImageVariant) -> float:
    # Blend structure, edges, and color to reject hash collisions without overfitting one signal.
    luminance = max(0.0, _block_ssim(left.gray, right.gray))
    left_gray = left.gray.astype(np.float32) / 255.0
    right_gray = right.gray.astype(np.float32) / 255.0
    left_edges = np.hypot(
        np.diff(left_gray, axis=1, append=left_gray[:, -1:]),
        np.diff(left_gray, axis=0, append=left_gray[-1:, :]),
    )
    right_edges = np.hypot(
        np.diff(right_gray, axis=1, append=right_gray[:, -1:]),
        np.diff(right_gray, axis=0, append=right_gray[-1:, :]),
    )
    edges = max(
        0.0,
        _block_ssim_normalized(left_edges / math.sqrt(2), right_edges / math.sqrt(2)),
    )
    color_difference = np.abs(left.rgb.astype(np.float32) - right.rgb.astype(np.float32))
    color = 1.0 - float(np.mean(color_difference)) / 255.0

    return 0.60 * luminance + 0.20 * edges + 0.20 * max(0.0, color)


def _variants_match(
    left: ImageVariant,
    right: ImageVariant,
    sensitivity: Sensitivity,
) -> float | None:
    if hamming_distance(left.perceptual_hash, right.perceptual_hash) > sensitivity.hash_distance:
        return None
    if hamming_distance(left.difference_hash, right.difference_hash) > sensitivity.difference_hash_distance:
        return None

    similarity = _pixel_similarity(left, right)
    if similarity < sensitivity.pixel_similarity:
        return None

    return similarity


def _best_match(
    left: ImageSignature,
    right: ImageSignature,
    candidate_pairs: Iterable[tuple[int, int, int]],
    sensitivity: Sensitivity,
    crop_tolerance: float,
) -> tuple[ImageVariant, ImageVariant, float] | None:
    best: tuple[ImageVariant, ImageVariant, float] | None = None
    seen: set[tuple[int, int]] = set()
    ordered_candidates = sorted(candidate_pairs)
    crop_evidence = False

    for _, left_variant_index, right_variant_index in ordered_candidates:
        key = (left_variant_index, right_variant_index)
        if key in seen:
            continue
        seen.add(key)

        left_variant = left.variants[left_variant_index]
        right_variant = right.variants[right_variant_index]

        # Comparing two cropped variants can join unrelated fragments; one side must stay complete.
        if left_variant_index != 0 and right_variant_index != 0:
            continue

        if (left_variant_index == 0) != (right_variant_index == 0):
            difference_distance = hamming_distance(left_variant.difference_hash, right_variant.difference_hash)
            if difference_distance <= sensitivity.difference_hash_distance + 16:
                crop_evidence = True

        similarity = _variants_match(left_variant, right_variant, sensitivity)
        if similarity is None:
            continue
        if best is None or similarity > best[2]:
            best = (left_variant, right_variant, similarity)

    if best is not None or crop_tolerance <= 0 or not crop_evidence:
        return best

    # Coarse variants found a plausible crop; align it more precisely before rejecting the pair.
    return _aligned_crop_match(left, right, sensitivity, crop_tolerance)


def _fraction_values(crop_tolerance: float, step: float = 0.025) -> list[float]:
    count = max(1, math.ceil(crop_tolerance / step))
    return [1.0 - crop_tolerance + crop_tolerance * index / count for index in range(count + 1)]


def _offset_values(maximum: float, step: float = 0.02) -> list[float]:
    if maximum <= 0:
        return [0.0]
    count = max(1, math.ceil(maximum / step))
    return [maximum * index / count for index in range(count + 1)]


def _candidate_crop_boxes(
    source: ImageSignature,
    target: ImageSignature,
    crop_tolerance: float,
) -> Iterable[tuple[float, float, float, float]]:
    source_aspect = source.width / source.height
    target_aspect = target.width / target.height
    aspect_ratio = target_aspect / source_aspect

    for height_fraction in _fraction_values(crop_tolerance):
        width_fraction = height_fraction * aspect_ratio
        if not 1.0 - crop_tolerance <= width_fraction <= 1.0:
            continue
        for left in _offset_values(1.0 - width_fraction):
            for top in _offset_values(1.0 - height_fraction):
                yield (
                    left,
                    top,
                    left + width_fraction,
                    top + height_fraction,
                )


def _quick_crop_similarity(
    image: Image.Image,
    box: tuple[float, float, float, float],
    target_gray: np.ndarray,
) -> float:
    width, height = image.size
    cropped = image.crop(
        (
            round(box[0] * width),
            round(box[1] * height),
            round(box[2] * width),
            round(box[3] * height),
        )
    )
    gray = np.asarray(
        cropped.convert("L").resize((32, 32), Image.Resampling.BILINEAR),
        dtype=np.float32,
    )
    mean_difference = float(np.mean(np.abs(gray - target_gray))) / 255.0
    gray_centered = gray - gray.mean()
    target_centered = target_gray - target_gray.mean()
    denominator = float(np.sqrt(np.sum(gray_centered**2) * np.sum(target_centered**2)))
    correlation = (
        float(np.sum(gray_centered * target_centered)) / denominator if denominator > 1e-6 else 1.0 - mean_difference
    )

    return 0.7 * max(0.0, correlation) + 0.3 * (1.0 - mean_difference)


def _aligned_direction_match(
    source: ImageSignature,
    target: ImageSignature,
    sensitivity: Sensitivity,
    crop_tolerance: float,
) -> tuple[ImageVariant, ImageVariant, float] | None:
    preview = Image.fromarray(source.preview)
    target_variant = target.variants[0]
    target_gray = np.asarray(
        Image.fromarray(target_variant.gray).resize((32, 32), Image.Resampling.BILINEAR),
        dtype=np.float32,
    )
    likely_boxes: list[tuple[float, tuple[float, float, float, float]]] = []

    # Rank cheap 32x32 comparisons, then run full checks only for the best alignments.
    for box in _candidate_crop_boxes(source, target, crop_tolerance):
        quick_similarity = _quick_crop_similarity(preview, box, target_gray)
        likely_boxes.append((quick_similarity, box))

    for _, box in sorted(likely_boxes, reverse=True)[:8]:
        source_variant = _image_variant(preview, box)
        match_similarity = _variants_match(source_variant, target_variant, sensitivity)
        if match_similarity is not None:
            return source_variant, target_variant, match_similarity

    return None


def _aligned_crop_match(
    left: ImageSignature,
    right: ImageSignature,
    sensitivity: Sensitivity,
    crop_tolerance: float,
) -> tuple[ImageVariant, ImageVariant, float] | None:
    left_source = _aligned_direction_match(left, right, sensitivity, crop_tolerance)
    right_source = _aligned_direction_match(right, left, sensitivity, crop_tolerance)
    if right_source is not None:
        right_variant, left_variant, similarity = right_source
        right_source = left_variant, right_variant, similarity

    matches = [match for match in (left_source, right_source) if match is not None]
    return max(matches, key=lambda match: match[2], default=None)


def _read_media(
    candidate: MediaCandidate,
    crop_tolerance: float,
    max_image_pixels: int,
) -> tuple[MediaFile, str | None]:
    path = candidate.path
    size = path.stat().st_size
    digest = sha256_file(path)
    signature = None
    error = None

    if candidate.compare_visually:
        try:
            signature = image_signature(
                path,
                crop_tolerance,
                max_image_pixels=max_image_pixels,
            )
        except (
            OSError,
            ValueError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ) as exception:
            error = f"Cannot decode image {path}: {exception}"

    return MediaFile(path, size, digest, signature), error


class ImagePixelLimitError(ValueError):
    def __init__(self, width: int, height: int, limit: int) -> None:
        super().__init__(f"Image dimensions {width}x{height} exceed pixel limit {limit}")


def _quality_key(file: MediaFile, extent_score: float) -> tuple[float, float, float, float, int]:
    if file.image is None:
        return (0.0, 0.0, 0.0, 0.0, file.size)

    pixels = file.image.width * file.image.height
    extent_factor = math.exp(max(-0.22, min(0.22, extent_score)))
    format_quality = {
        "BMP": 2.0,
        "PNG": 2.0,
        "TIFF": 2.0,
        "WEBP": 1.0,
        "GIF": 0.5,
        "JPEG": 0.0,
    }.get(file.image.format_name, 1.0)
    return (
        pixels * extent_factor,
        min(file.image.width, file.image.height),
        format_quality,
        file.image.sharpness,
        file.size,
    )


def find_duplicate_groups(
    candidates: Iterable[MediaCandidate],
    *,
    sensitivity: Sensitivity = SENSITIVITIES["balanced"],
    crop_tolerance: float = 0.20,
    max_image_pixels: int = 40_000_000,
) -> ScanResult:
    if not 0 <= crop_tolerance <= 0.40:
        raise ValueError("crop_tolerance must be between 0 and 0.40")
    if max_image_pixels <= 0:
        raise ValueError("max_image_pixels must be positive")

    candidates = tuple(candidates)
    logger.debug("Analyzing %d media files", len(candidates))

    files: dict[int, MediaFile] = {}
    errors: list[str] = []
    unions = UnionFind(len(candidates))
    exact_files: dict[tuple[int, str], int] = {}
    tree = HammingBKTree()
    image_matches: list[ImageMatch] = []

    for file_index, candidate in enumerate(candidates):
        logger.debug("Reading media file: %s", candidate.path)
        try:
            file, error = _read_media(candidate, crop_tolerance, max_image_pixels)
        except OSError as exception:
            message = f"Cannot read {candidate.path}: {exception}"
            errors.append(message)
            logger.error("%s", message)
            continue

        files[file_index] = file
        if error:
            errors.append(error)
            logger.error("%s", error)

        key = (file.size, file.digest)
        duplicate_of = exact_files.setdefault(key, file_index)
        if unions.union(file_index, duplicate_of):
            logger.debug(
                "Exact duplicate: %s matches %s",
                file.path,
                files[duplicate_of].path,
            )

        if file.image is not None:
            # The BK-tree limits expensive visual comparisons to nearby perceptual hashes.
            visual_candidates: dict[int, list[tuple[int, int, int]]] = defaultdict(list)
            for variant_index, variant in enumerate(file.image.variants):
                for distance, other_file_index, other_variant_index in tree.search(
                    variant.perceptual_hash, sensitivity.hash_distance
                ):
                    visual_candidates[other_file_index].append((distance, variant_index, other_variant_index))

            for other_file_index, candidate_pairs in visual_candidates.items():
                other = files[other_file_index]
                if other.image is None:
                    continue
                best_match = _best_match(
                    file.image,
                    other.image,
                    candidate_pairs,
                    sensitivity,
                    crop_tolerance,
                )
                if best_match is None:
                    continue

                left_variant, right_variant, similarity = best_match
                unions.union(file_index, other_file_index)
                logger.debug(
                    "Visual duplicate: %s matches %s with similarity %.3f",
                    file.path,
                    other.path,
                    similarity,
                )
                image_matches.append(
                    ImageMatch(
                        file_index,
                        other_file_index,
                        left_variant.coverage,
                        right_variant.coverage,
                        similarity,
                    )
                )

            for variant_index, variant in enumerate(file.image.variants):
                tree.add(variant.perceptual_hash, (file_index, variant_index))

    grouped_indices: dict[int, list[int]] = defaultdict(list)
    for index in files:
        grouped_indices[unions.find(index)].append(index)

    extent_scores = [0.0] * len(candidates)
    match_degrees = [0] * len(candidates)

    # Crop coverage estimates which copy contains more of the original scene.
    for image_match in image_matches:
        relative_extent = math.log(image_match.right_coverage / image_match.left_coverage)
        extent_scores[image_match.left_index] += relative_extent
        extent_scores[image_match.right_index] -= relative_extent
        match_degrees[image_match.left_index] += 1
        match_degrees[image_match.right_index] += 1
    for index, degree in enumerate(match_degrees):
        if degree:
            extent_scores[index] /= degree

    groups = []

    for indices in grouped_indices.values():
        if len(indices) < 2:
            continue
        kept_index = max(
            indices,
            key=lambda index: _quality_key(files[index], extent_scores[index]),
        )
        duplicate_indices = sorted(
            (index for index in indices if index != kept_index),
            key=lambda index: str(files[index].path).casefold(),
        )
        groups.append(
            DuplicateGroup(
                kept=files[kept_index],
                duplicates=tuple(files[index] for index in duplicate_indices),
            )
        )
        logger.debug(
            "Selected %s as the best copy among %d files",
            files[kept_index].path,
            len(indices),
        )

    groups.sort(key=lambda group: str(group.kept.path).casefold())
    ordered_files = tuple(files[index] for index in sorted(files))

    return ScanResult(ordered_files, tuple(groups), tuple(errors))
