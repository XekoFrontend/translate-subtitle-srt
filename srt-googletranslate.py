# pip install deep-translator

import re
import sys
import json
import time
from pathlib import Path

from deep_translator import GoogleTranslator


# ============================================================
# DEFAULT CONFIG
# ============================================================

DEFAULT_SOURCE = "zh-CN"
DEFAULT_TARGET = "vi"

DEFAULT_BATCH_SIZE = 8
DEFAULT_RETRY = 3

# 0 = không cố tình delay
DEFAULT_DELAY = 0


# ============================================================
# UI
# ============================================================

def banner():
    print()
    print("╔════════════════════════════════════════════════════════════╗")
    print("║                       srtTrans v2                         ║")
    print("║              Google Translate Batch Tool                  ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print()


def ask(prompt, default=None):
    if default is not None:
        value = input(f"{prompt} [{default}]: ").strip()
        return value if value else str(default)

    return input(f"{prompt}: ").strip()


def ask_int(prompt, default, minimum=1):
    while True:
        value = ask(prompt, default)

        try:
            value = int(value)

            if value >= minimum:
                return value

        except ValueError:
            pass

        print(f"⚠ Nhập số >= {minimum}.")


def confirm(prompt, default=True):
    default_text = "Y/n" if default else "y/N"

    value = input(
        f"{prompt} [{default_text}]: "
    ).strip().lower()

    if not value:
        return default

    return value in ("y", "yes")


# ============================================================
# FILE READING
# ============================================================

def read_text(path):
    encodings = [
        "utf-8-sig",
        "utf-8",
        "gb18030",
        "gbk",
        "big5",
    ]

    for encoding in encodings:
        try:
            return Path(path).read_text(
                encoding=encoding
            )
        except UnicodeDecodeError:
            pass

    raise RuntimeError(
        f"Không đọc được file: {path}"
    )


# ============================================================
# PROMPT / SETTINGS
# ============================================================

def load_prompt():
    """
    prompt.txt không phải AI prompt.
    Google Translate không hỗ trợ prompt.

    File này có thể chứa:
        SOURCE = zh-CN
        TARGET = vi
        BATCH = 8
        DELAY = 0

    Các dòng bắt đầu bằng # được bỏ qua.
    """

    path = Path("prompt.txt")

    if not path.exists():
        return {}

    settings = {}

    for line in path.read_text(
        encoding="utf-8-sig"
    ).splitlines():

        line = line.strip()

        if not line or line.startswith("#"):
            continue

        if "=" not in line:
            continue

        key, value = line.split(
            "=",
            1
        )

        settings[key.strip().upper()] = (
            value.strip()
        )

    return settings


# ============================================================
# SRT PARSER
# ============================================================

def parse_srt(content):

    content = content.replace(
        "\r\n",
        "\n"
    )

    content = content.replace(
        "\r",
        "\n"
    )

    blocks = re.split(
        r"\n\s*\n",
        content.strip()
    )

    subtitles = []

    for block in blocks:

        lines = block.split("\n")

        if len(lines) < 3:
            continue

        index = lines[0].strip()
        timestamp = lines[1].strip()

        if not index.isdigit():
            continue

        if "-->" not in timestamp:
            continue

        text = "\n".join(
            lines[2:]
        ).strip()

        subtitles.append({
            "index": int(index),
            "timestamp": timestamp,
            "text": text,
        })

    return subtitles


# ============================================================
# HTML / SRT TAG PROTECTION
# ============================================================

def protect_tags(text):

    tags = []

    def replace(match):
        token = (
            f"ZZZSRTTAG{len(tags)}ZZZ"
        )

        tags.append(
            match.group(0)
        )

        return token

    protected = re.sub(
        r"<[^>]+>",
        replace,
        text
    )

    return protected, tags


def restore_tags(text, tags):

    for i, tag in enumerate(tags):

        token = (
            f"ZZZSRTTAG{i}ZZZ"
        )

        text = text.replace(
            token,
            tag
        )

    return text


# ============================================================
# BUILD BATCH
# ============================================================

def build_batch_text(batch):
    """
    Tạo một request duy nhất cho nhiều subtitle.

    Marker phải đủ đặc biệt để Google Translate
    ít có khả năng thay đổi.
    """

    parts = []

    for sub in batch:

        protected, tags = protect_tags(
            sub["text"]
        )

        sub["_tags"] = tags

        marker = (
            f"\n\n"
            f"ZZZSRTSUB{str(sub['index']).zfill(6)}ZZZ"
            f"\n"
        )

        parts.append(
            marker + protected
        )

    return "".join(parts)


# ============================================================
# PARSE TRANSLATED BATCH
# ============================================================

def parse_batch_result(batch, translated_text):

    result = {}

    pattern = re.compile(
        r"ZZZSRTSUB(\d{6})ZZZ"
    )

    matches = list(
        pattern.finditer(translated_text)
    )

    if not matches:
        raise RuntimeError(
            "Google Translate làm mất marker."
        )

    for pos, match in enumerate(matches):

        index = int(
            match.group(1)
        )

        start = match.end()

        if pos + 1 < len(matches):
            end = matches[pos + 1].start()
        else:
            end = len(translated_text)

        text = translated_text[
            start:end
        ].strip()

        result[index] = text

    expected = {
        sub["index"]
        for sub in batch
    }

    actual = set(
        result.keys()
    )

    missing = expected - actual

    if missing:
        raise RuntimeError(
            "Thiếu subtitle sau khi tách: "
            + ", ".join(
                map(str, sorted(missing))
            )
        )

    # Restore tags
    for sub in batch:

        index = sub["index"]

        result[index] = restore_tags(
            result[index],
            sub.get("_tags", [])
        )

    return result


# ============================================================
# CACHE
# ============================================================

def get_cache_dir(input_path):

    cache_dir = (
        input_path.parent
        / ".srtTrans_cache"
        / input_path.stem
    )

    cache_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    return cache_dir


def cache_file(cache_dir, batch_number):

    return (
        cache_dir
        / f"batch_{batch_number:05d}.json"
    )


def load_cached_batch(
    cache_dir,
    batch_number
):

    path = cache_file(
        cache_dir,
        batch_number
    )

    if not path.exists():
        return None

    try:

        data = json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )

        return data

    except Exception:

        return None


def save_cached_batch(
    cache_dir,
    batch_number,
    translations
):

    path = cache_file(
        cache_dir,
        batch_number
    )

    path.write_text(
        json.dumps(
            translations,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


# ============================================================
# GOOGLE TRANSLATE
# ============================================================

def translate_batch(
    translator,
    batch,
    retry_count
):

    source_text = build_batch_text(
        batch
    )

    last_error = None

    for attempt in range(
        1,
        retry_count + 1
    ):

        try:

            translated = translator.translate(
                source_text
            )

            if not translated:
                raise RuntimeError(
                    "Google trả về kết quả rỗng."
                )

            return parse_batch_result(
                batch,
                translated
            )

        except Exception as e:

            last_error = e

            print()
            print(
                f"  ⚠ Batch lỗi "
                f"(lần {attempt}/{retry_count})"
            )

            print(
                f"    {e}"
            )

            if attempt < retry_count:

                wait = attempt * 2

                print(
                    f"    → Thử lại sau {wait}s..."
                )

                time.sleep(wait)

    raise RuntimeError(
        f"Batch thất bại sau "
        f"{retry_count} lần: {last_error}"
    )


# ============================================================
# OUTPUT
# ============================================================

def output_path_for(input_path, target):

    return (
        input_path.parent
        / f"{input_path.stem}.{target}.srt"
    )


def write_srt(
    subtitles,
    output_path
):

    blocks = []

    for sub in subtitles:

        blocks.append(
            "\n".join([
                str(sub["index"]),
                sub["timestamp"],
                sub["text"],
            ])
        )

    content = (
        "\n\n".join(blocks)
        + "\n"
    )

    output_path.write_text(
        content,
        encoding="utf-8-sig"
    )


# ============================================================
# PROGRESS BAR
# ============================================================

def show_progress(
    current,
    total,
    batch_number,
    total_batches
):

    percent = (
        current / total * 100
    )

    width = 35

    filled = int(
        width * current / total
    )

    bar = (
        "█" * filled
        + "░" * (width - filled)
    )

    print(
        f"\r[{bar}] "
        f"{percent:6.2f}% "
        f"{current:,}/{total:,} "
        f"| Batch {batch_number}/{total_batches}",
        end="",
        flush=True
    )


# ============================================================
# TRANSLATE FILE
# ============================================================

def translate_file(
    input_path,
    source,
    target,
    batch_size,
    retry_count,
    delay
):

    output_path = output_path_for(
        input_path,
        target
    )

    cache_dir = get_cache_dir(
        input_path
    )

    print()
    print(
        "Đang đọc SRT..."
    )

    content = read_text(
        input_path
    )

    subtitles = parse_srt(
        content
    )

    if not subtitles:

        raise RuntimeError(
            "Không tìm thấy subtitle hợp lệ."
        )

    total = len(subtitles)

    total_batches = (
        total + batch_size - 1
    ) // batch_size

    print(
        f"✓ Subtitle : {total:,}"
    )

    print(
        f"✓ Batch    : {batch_size}"
    )

    print(
        f"✓ Tổng batch: {total_batches:,}"
    )

    print(
        f"✓ Cache    : {cache_dir}"
    )

    print(
        f"✓ Output   : {output_path.name}"
    )

    print()

    translator = GoogleTranslator(
        source=source,
        target=target
    )

    completed = 0
    cached_count = 0
    translated_count = 0

    # --------------------------------------------------------
    # BATCH LOOP
    # --------------------------------------------------------

    for batch_number, start in enumerate(
        range(
            0,
            total,
            batch_size
        ),
        start=1
    ):

        batch = subtitles[
            start:
            start + batch_size
        ]

        first_index = batch[0]["index"]
        last_index = batch[-1]["index"]

        # ----------------------------------------------------
        # CACHE
        # ----------------------------------------------------

        cached = load_cached_batch(
            cache_dir,
            batch_number
        )

        if cached is not None:

            for sub in batch:

                index = str(
                    sub["index"]
                )

                if index in cached:
                    sub["text"] = cached[index]

            completed += len(batch)
            cached_count += 1

            print(
                f"\r✓ CACHE "
                f"Batch {batch_number}/{total_batches} "
                f"({first_index}-{last_index})"
                + " " * 20
            )

            continue

        # ----------------------------------------------------
        # TRANSLATE
        # ----------------------------------------------------

        print(
            f"\n→ Batch "
            f"{batch_number}/{total_batches} "
            f"({first_index}-{last_index})"
        )

        translations = translate_batch(
            translator,
            batch,
            retry_count
        )

        # ----------------------------------------------------
        # APPLY
        # ----------------------------------------------------

        cache_data = {}

        for sub in batch:

            index = sub["index"]

            translated = translations[
                index
            ]

            sub["text"] = translated

            cache_data[
                str(index)
            ] = translated

        # ----------------------------------------------------
        # SAVE CACHE IMMEDIATELY
        # ----------------------------------------------------

        save_cached_batch(
            cache_dir,
            batch_number,
            cache_data
        )

        translated_count += 1
        completed += len(batch)

        show_progress(
            completed,
            total,
            batch_number,
            total_batches
        )

        # ----------------------------------------------------
        # OPTIONAL DELAY
        # ----------------------------------------------------

        if delay > 0:
            time.sleep(delay)

    print()
    print()

    # --------------------------------------------------------
    # WRITE OUTPUT
    # --------------------------------------------------------

    print(
        "Đang ghi SRT..."
    )

    write_srt(
        subtitles,
        output_path
    )

    print()
    print("╔════════════════════════════════════════════════════════════╗")
    print("║                    ✓ HOÀN THÀNH                          ║")
    print("╚════════════════════════════════════════════════════════════╝")
    print()

    print(
        f"File gốc      : {input_path.name}"
    )

    print(
        f"File dịch     : {output_path.name}"
    )

    print(
        f"Tổng subtitle : {total:,}"
    )

    print(
        f"Batch mới     : {translated_count:,}"
    )

    print(
        f"Batch cache   : {cached_count:,}"
    )

    print()

    print(
        "Cache được giữ lại để lần sau Resume."
    )

    print()


# ============================================================
# MAIN
# ============================================================

def main():

    banner()

    settings = load_prompt()

    # --------------------------------------------------------
    # INPUT FILE
    # --------------------------------------------------------

    if len(sys.argv) >= 2:

        input_file = Path(
            sys.argv[1]
        )

    else:

        srt_files = sorted(
            Path(".").glob("*.srt")
        )

        if not srt_files:

            print(
                "❌ Không tìm thấy file .srt"
            )

            print()
            print(
                "Cách dùng:"
            )

            print(
                "  python srt_translate.py phim.srt"
            )

            return

        if len(srt_files) == 1:

            input_file = srt_files[0]

        else:

            print(
                "Các file SRT:"
            )

            print()

            for i, path in enumerate(
                srt_files,
                1
            ):

                print(
                    f"  [{i}] {path.name}"
                )

            print()

            choice = ask_int(
                "Chọn file",
                1
            )

            if choice > len(srt_files):

                print(
                    "❌ Lựa chọn không hợp lệ."
                )

                return

            input_file = (
                srt_files[choice - 1]
            )

    if not input_file.exists():

        print(
            f"❌ Không tìm thấy: "
            f"{input_file}"
        )

        return

    # --------------------------------------------------------
    # SETTINGS
    # --------------------------------------------------------

    source = settings.get(
        "SOURCE",
        DEFAULT_SOURCE
    )

    target = settings.get(
        "TARGET",
        DEFAULT_TARGET
    )

    batch_size = int(
        settings.get(
            "BATCH",
            DEFAULT_BATCH_SIZE
        )
    )

    retry_count = int(
        settings.get(
            "RETRY",
            DEFAULT_RETRY
        )
    )

    delay = float(
        settings.get(
            "DELAY",
            DEFAULT_DELAY
        )
    )

    # --------------------------------------------------------
    # UX
    # --------------------------------------------------------

    print()
    print("────────────────────────────────────────────────────────────")
    print(" CẤU HÌNH")
    print("────────────────────────────────────────────────────────────")

    print(
        f"File       : {input_file.name}"
    )

    source = ask(
        "Ngôn ngữ gốc",
        source
    )

    target = ask(
        "Ngôn ngữ đích",
        target
    )

    batch_size = ask_int(
        "Subtitle mỗi batch",
        batch_size,
        minimum=1
    )

    retry_count = ask_int(
        "Số lần retry",
        retry_count,
        minimum=1
    )

    delay = float(
        ask(
            "Delay giữa batch",
            delay
        )
    )

    print()
    print("────────────────────────────────────────────────────────────")
    print(" XÁC NHẬN")
    print("────────────────────────────────────────────────────────────")

    print(
        f"Input       : {input_file.name}"
    )

    print(
        f"From        : {source}"
    )

    print(
        f"To          : {target}"
    )

    print(
        f"Batch       : {batch_size}"
    )

    print(
        f"Retry       : {retry_count}"
    )

    print(
        f"Delay       : {delay}s"
    )

    print(
        "Cache       : ON"
    )

    print()

    if not confirm(
        "Bắt đầu dịch?",
        True
    ):

        print(
            "Đã hủy."
        )

        return

    # --------------------------------------------------------
    # START
    # --------------------------------------------------------

    try:

        translate_file(
            input_path=input_file,
            source=source,
            target=target,
            batch_size=batch_size,
            retry_count=retry_count,
            delay=delay
        )

    except KeyboardInterrupt:

        print()
        print()
        print(
            "⚠ Đã dừng bằng Ctrl+C."
        )

        print(
            "Các batch đã hoàn thành vẫn được giữ trong cache."
        )

        print(
            "Chạy lại tool để tiếp tục."
        )

    except Exception as e:

        print()
        print(
            f"❌ LỖI: {e}"
        )

        print()


if __name__ == "__main__":
    main()