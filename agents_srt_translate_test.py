#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Agents AI Gateway - SRT Translator TEST

- Dùng OpenAI-compatible API:
    GET  {BASE_URL}/models
    POST {BASE_URL}/chat/completions
- Nhập Base URL + API key
- Tự dò và liệt kê model
- Cho user chọn model
- Cho user nhập system prompt / translation prompt tùy ý
- Dịch SRT theo batch
- Giữ nguyên số thứ tự + timestamp
- Bảo vệ HTML/SRT tags
- Cache từng batch để resume
- Không lưu API key
"""

import re
import sys
import json
import time
import getpass
from pathlib import Path

import requests


DEFAULT_BASE_URL = "https://gateway.agents.ai.vn/v1"
DEFAULT_SOURCE = "zh-CN"
DEFAULT_TARGET = "vi"
DEFAULT_BATCH_SIZE = 8
DEFAULT_RETRY = 3
DEFAULT_DELAY = 0.2
TIMEOUT = 120


# ============================================================
# UI
# ============================================================

def banner():
    print()
    print("╔════════════════════════════════════════════════════════════╗")
    print("║              AGENTS AI SRT TRANSLATOR TEST               ║")
    print("║          OpenAI-compatible Gateway / Chat API            ║")
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
    value = input(
        f"{prompt} [{'Y/n' if default else 'y/N'}]: "
    ).strip().lower()

    if not value:
        return default

    return value in ("y", "yes")


def multiline_prompt(title, default=""):
    print()
    print("─" * 70)
    print(title)
    print("Nhập nhiều dòng. Gõ một dòng chỉ gồm END để kết thúc.")
    if default:
        print("\nMặc định:")
        print(default)
    print("─" * 70)

    lines = []

    while True:
        line = input()

        if line.strip() == "END":
            break

        lines.append(line)

    result = "\n".join(lines).strip()

    if not result:
        return default

    return result


# ============================================================
# API
# ============================================================

def headers(api_key):
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "User-Agent": "Agents-AI-SRT-Test/1.0",
    }


def get_models(base_url, api_key):
    url = base_url.rstrip("/") + "/models"

    print(f"\n[1] GET {url}")
    print("-" * 70)

    try:
        start = time.perf_counter()

        r = requests.get(
            url,
            headers=headers(api_key),
            timeout=TIMEOUT,
        )

        elapsed = time.perf_counter() - start

        print(f"HTTP: {r.status_code}")
        print(f"Time: {elapsed:.2f}s")

        try:
            data = r.json()
        except ValueError:
            print("\n[✗] Server không trả JSON:")
            print(r.text[:10000])
            return None

        if not r.ok:
            print("\n[✗] /models FAILED")
            print(json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ))
            return None

        models = data.get("data", [])

        if not isinstance(models, list):
            print("\n[!] Trường data không phải list.")
            print(json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            ))
            return None

        print(f"\n[✓] Tìm thấy {len(models)} model:\n")

        for i, model in enumerate(models, 1):
            model_id = model.get("id", "?")
            owner = model.get("owned_by", "")
            print(
                f"  [{i:02d}] {model_id}"
                + (f"   ({owner})" if owner else "")
            )

        return models

    except requests.exceptions.RequestException as e:
        print(f"\n[✗] Không kết nối được: {e}")
        return None


def choose_model(models):
    print()
    print("─" * 70)
    print("CHỌN MODEL")
    print("─" * 70)

    while True:
        value = input(
            "Nhập số hoặc Model ID [1]: "
        ).strip()

        if not value:
            value = "1"

        if value.isdigit():
            index = int(value)

            if 1 <= index <= len(models):
                return models[index - 1].get("id")

            print("⚠ Số không hợp lệ.")
            continue

        ids = [
            str(x.get("id", ""))
            for x in models
        ]

        if value in ids:
            return value

        print("⚠ Không tìm thấy model ID.")


def extract_content(data):
    """
    Hỗ trợ nhiều dạng response phổ biến.
    """

    # OpenAI Chat Completions
    choices = data.get("choices")

    if isinstance(choices, list) and choices:
        choice = choices[0]

        message = choice.get("message")

        if isinstance(message, dict):
            content = message.get("content")

            if isinstance(content, str):
                return content

            # Một số gateway có thể trả content dạng list
            if isinstance(content, list):
                parts = []

                for item in content:
                    if isinstance(item, dict):
                        text = item.get("text")
                        if isinstance(text, str):
                            parts.append(text)

                if parts:
                    return "".join(parts)

            reasoning = message.get("reasoning")

            if isinstance(reasoning, str) and reasoning.strip():
                return reasoning

        text = choice.get("text")

        if isinstance(text, str):
            return text

    # Một số API khác
    for key in ("output_text", "output", "text"):
        value = data.get(key)

        if isinstance(value, str):
            return value

    return None


def chat_request(
    base_url,
    api_key,
    model,
    messages,
    temperature=0.2,
):
    url = base_url.rstrip("/") + "/chat/completions"

    payload = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }

    start = time.perf_counter()

    r = requests.post(
        url,
        headers=headers(api_key),
        json=payload,
        timeout=TIMEOUT,
    )

    elapsed = time.perf_counter() - start

    try:
        data = r.json()
    except ValueError:
        raise RuntimeError(
            f"HTTP {r.status_code}: server không trả JSON:\n"
            + r.text[:5000]
        )

    return r, data, elapsed


# ============================================================
# PROMPT
# ============================================================

DEFAULT_SYSTEM_PROMPT = """You are a professional subtitle translator.

Translate subtitles from the source language into Vietnamese.

Rules:
1. Preserve the meaning and context.
2. Use natural Vietnamese suitable for movie/TV subtitles.
3. Do not translate subtitle markers such as ZZZSRTSUB000001ZZZ.
4. Do not add explanations.
5. Do not add quotation marks unless they exist in the original.
6. Preserve line breaks when reasonable.
7. Preserve names and terminology consistently.
8. Return ONLY the translated subtitles."""


def build_user_prompt(batch, source, target):
    parts = [
        f"Translate from {source} to {target}.",
        "",
        "IMPORTANT:",
        "- Keep every ZZZSRTSUBxxxxxxZZZ marker exactly unchanged.",
        "- Do not remove, rename, reorder, or create markers.",
        "- Return the markers and translated text only.",
        "",
    ]

    for sub in batch:
        parts.append(
            f"ZZZSRTSUB{str(sub['index']).zfill(6)}ZZZ"
        )
        parts.append(sub["_protected_text"])
        parts.append("")

    return "\n".join(parts).strip()


# ============================================================
# SRT
# ============================================================

def read_text(path):
    for encoding in (
        "utf-8-sig",
        "utf-8",
        "gb18030",
        "gbk",
        "big5",
    ):
        try:
            return Path(path).read_text(
                encoding=encoding
            )
        except UnicodeDecodeError:
            pass

    raise RuntimeError(
        f"Không đọc được file: {path}"
    )


def parse_srt(content):
    content = content.replace("\r\n", "\n")
    content = content.replace("\r", "\n")

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


def protect_tags(text):
    tags = []

    def replace(match):
        token = f"ZZZSRTTAG{len(tags)}ZZZ"
        tags.append(match.group(0))
        return token

    protected = re.sub(
        r"<[^>]+>",
        replace,
        text
    )

    return protected, tags


def restore_tags(text, tags):
    for i, tag in enumerate(tags):
        token = f"ZZZSRTTAG{i}ZZZ"
        text = text.replace(token, tag)

    return text


# ============================================================
# BATCH
# ============================================================

def prepare_batch(batch):
    for sub in batch:
        protected, tags = protect_tags(
            sub["text"]
        )

        sub["_tags"] = tags
        sub["_protected_text"] = protected


def parse_batch_result(batch, translated_text):
    pattern = re.compile(
        r"ZZZSRTSUB(\d{6})ZZZ"
    )

    matches = list(
        pattern.finditer(translated_text)
    )

    if not matches:
        raise RuntimeError(
            "Model làm mất toàn bộ subtitle marker."
        )

    result = {}

    for pos, match in enumerate(matches):
        index = int(match.group(1))

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

    actual = set(result.keys())

    missing = expected - actual
    extra = actual - expected

    if missing:
        raise RuntimeError(
            "Thiếu subtitle: "
            + ", ".join(
                map(str, sorted(missing))
            )
        )

    if extra:
        raise RuntimeError(
            "Model tạo marker không tồn tại: "
            + ", ".join(
                map(str, sorted(extra))
            )
        )

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
        / ".agents_srt_cache"
        / input_path.stem
    )

    cache_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    return cache_dir


def cache_file(cache_dir, batch_number):
    return cache_dir / (
        f"batch_{batch_number:05d}.json"
    )


def load_cache(cache_dir, batch_number):
    path = cache_file(
        cache_dir,
        batch_number
    )

    if not path.exists():
        return None

    try:
        return json.loads(
            path.read_text(
                encoding="utf-8"
            )
        )
    except Exception:
        return None


def save_cache(
    cache_dir,
    batch_number,
    translations,
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
# TRANSLATE BATCH
# ============================================================

def translate_batch(
    base_url,
    api_key,
    model,
    batch,
    source,
    target,
    system_prompt,
    retry_count,
):
    prepare_batch(batch)

    user_prompt = build_user_prompt(
        batch,
        source,
        target
    )

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": user_prompt,
        },
    ]

    last_error = None

    for attempt in range(
        1,
        retry_count + 1
    ):
        try:
            r, data, elapsed = chat_request(
                base_url,
                api_key,
                model,
                messages,
            )

            print(
                f"    HTTP {r.status_code} | "
                f"{elapsed:.2f}s"
            )

            if not r.ok:
                raise RuntimeError(
                    "API error:\n"
                    + json.dumps(
                        data,
                        ensure_ascii=False,
                        indent=2
                    )[:8000]
                )

            translated = extract_content(
                data
            )

            if not translated:
                raise RuntimeError(
                    "API trả response nhưng "
                    "không tìm thấy nội dung completion.\n"
                    "Raw response:\n"
                    + json.dumps(
                        data,
                        ensure_ascii=False,
                        indent=2
                    )[:10000]
                )

            return parse_batch_result(
                batch,
                translated
            )

        except Exception as e:
            last_error = e

            print(
                f"    ⚠ Batch lỗi "
                f"({attempt}/{retry_count})"
            )
            print(
                f"    {e}"
            )

            if attempt < retry_count:
                wait = attempt * 2
                print(
                    f"    → retry sau {wait}s..."
                )
                time.sleep(wait)

    raise RuntimeError(
        f"Batch thất bại sau {retry_count} lần: "
        f"{last_error}"
    )


# ============================================================
# OUTPUT
# ============================================================

def output_path_for(input_path, target):
    return (
        input_path.parent
        / f"{input_path.stem}.{target}.srt"
    )


def write_srt(subtitles, output_path):
    blocks = []

    for sub in subtitles:
        blocks.append(
            "\n".join([
                str(sub["index"]),
                sub["timestamp"],
                sub["text"],
            ])
        )

    output_path.write_text(
        "\n\n".join(blocks) + "\n",
        encoding="utf-8-sig"
    )


# ============================================================
# FILE CHOICE
# ============================================================

def choose_input_file():
    if len(sys.argv) >= 2:
        return Path(sys.argv[1])

    files = sorted(
        Path(".").glob("*.srt")
    )

    if not files:
        print(
            "❌ Không tìm thấy .srt trong thư mục hiện tại."
        )
        return None

    if len(files) == 1:
        return files[0]

    print("\nCác file SRT:\n")

    for i, path in enumerate(files, 1):
        print(f"  [{i}] {path.name}")

    print()

    choice = ask_int(
        "Chọn file",
        1
    )

    if choice > len(files):
        return None

    return files[choice - 1]


# ============================================================
# QUICK CHAT TEST
# ============================================================

def quick_chat_test(
    base_url,
    api_key,
    model,
    system_prompt,
):
    print()
    print("═" * 70)
    print("TEST CHAT NHANH TRƯỚC KHI DỊCH")
    print("═" * 70)

    prompt = input(
        "Prompt test [Enter = Xin chào, hãy trả lời bằng tiếng Việt.]: "
    ).strip()

    if not prompt:
        prompt = (
            "Xin chào, hãy trả lời bằng tiếng Việt."
        )

    messages = [
        {
            "role": "system",
            "content": system_prompt,
        },
        {
            "role": "user",
            "content": prompt,
        },
    ]

    try:
        r, data, elapsed = chat_request(
            base_url,
            api_key,
            model,
            messages,
        )

        print(f"\nHTTP: {r.status_code}")
        print(f"Time: {elapsed:.2f}s")

        print("\nRaw response:")
        print(
            json.dumps(
                data,
                ensure_ascii=False,
                indent=2
            )[:15000]
        )

        content = extract_content(data)

        print("\n" + "─" * 70)

        if content:
            print("MODEL:")
            print(content)
            print("─" * 70)

            return True

        print(
            "❌ Không tìm thấy content trong response."
        )

        return False

    except Exception as e:
        print(f"\n❌ Chat test lỗi: {e}")
        return False


# ============================================================
# TRANSLATE FILE
# ============================================================

def translate_file(
    input_path,
    base_url,
    api_key,
    model,
    source,
    target,
    batch_size,
    retry_count,
    delay,
    system_prompt,
):
    content = read_text(input_path)

    subtitles = parse_srt(content)

    if not subtitles:
        raise RuntimeError(
            "Không tìm thấy subtitle hợp lệ."
        )

    total = len(subtitles)

    total_batches = (
        total + batch_size - 1
    ) // batch_size

    output_path = output_path_for(
        input_path,
        target
    )

    cache_dir = get_cache_dir(
        input_path
    )

    print("\n" + "═" * 70)
    print("DỊCH SRT")
    print("═" * 70)

    print(f"Input       : {input_path.name}")
    print(f"Model       : {model}")
    print(f"Source      : {source}")
    print(f"Target      : {target}")
    print(f"Subtitle    : {total:,}")
    print(f"Batch       : {batch_size}")
    print(f"Total batch : {total_batches}")
    print(f"Cache       : {cache_dir}")
    print(f"Output      : {output_path.name}")

    completed = 0
    cached_count = 0
    translated_count = 0

    for batch_number, start in enumerate(
        range(
            0,
            total,
            batch_size
        ),
        1
    ):
        batch = subtitles[
            start:start + batch_size
        ]

        first_index = batch[0]["index"]
        last_index = batch[-1]["index"]

        cached = load_cache(
            cache_dir,
            batch_number
        )

        if cached is not None:
            valid = True

            for sub in batch:
                key = str(sub["index"])

                if key not in cached:
                    valid = False
                    break

            if valid:
                for sub in batch:
                    sub["text"] = cached[
                        str(sub["index"])
                    ]

                completed += len(batch)
                cached_count += 1

                print(
                    f"[CACHE] "
                    f"Batch {batch_number}/{total_batches} "
                    f"({first_index}-{last_index})"
                )

                continue

        print(
            f"\n[BATCH {batch_number}/{total_batches}] "
            f"Subtitle {first_index}-{last_index}"
        )

        translations = translate_batch(
            base_url=base_url,
            api_key=api_key,
            model=model,
            batch=batch,
            source=source,
            target=target,
            system_prompt=system_prompt,
            retry_count=retry_count,
        )

        cache_data = {}

        for sub in batch:
            index = sub["index"]

            sub["text"] = translations[index]

            cache_data[
                str(index)
            ] = translations[index]

        save_cache(
            cache_dir,
            batch_number,
            cache_data
        )

        completed += len(batch)
        translated_count += 1

        percent = (
            completed / total * 100
        )

        print(
            f"    ✓ {completed}/{total} "
            f"({percent:.1f}%)"
        )

        if delay > 0:
            time.sleep(delay)

    write_srt(
        subtitles,
        output_path
    )

    print("\n" + "═" * 70)
    print("✓ HOÀN THÀNH")
    print("═" * 70)

    print(f"Output        : {output_path}")
    print(f"Subtitle      : {total:,}")
    print(f"Batch mới     : {translated_count}")
    print(f"Batch cache   : {cached_count}")
    print(f"Cache         : {cache_dir}")
    print("═" * 70)


# ============================================================
# MAIN
# ============================================================

def main():
    banner()

    input_file = choose_input_file()

    if input_file is None:
        return

    if not input_file.exists():
        print(f"❌ Không tìm thấy: {input_file}")
        return

    print("\n" + "═" * 70)
    print("API CONFIG")
    print("═" * 70)

    base_url = ask(
        "Base URL",
        DEFAULT_BASE_URL
    ).rstrip("/")

    print(
        "\nAPI key sẽ được nhập ẩn và KHÔNG ghi ra file."
    )

    api_key = getpass.getpass(
        "API Key: "
    ).strip()

    if not api_key:
        print("❌ API key trống.")
        return

    models = get_models(
        base_url,
        api_key
    )

    if models is None or not models:
        return

    model = choose_model(models)

    print(
        f"\n✓ Model được chọn: {model}"
    )

    source = ask(
        "Ngôn ngữ gốc",
        DEFAULT_SOURCE
    )

    target = ask(
        "Ngôn ngữ đích",
        DEFAULT_TARGET
    )

    batch_size = ask_int(
        "Subtitle mỗi batch",
        DEFAULT_BATCH_SIZE
    )

    retry_count = ask_int(
        "Retry",
        DEFAULT_RETRY
    )

    delay = float(
        ask(
            "Delay giữa batch (giây)",
            DEFAULT_DELAY
        )
    )

    # --------------------------------------------------------
    # CUSTOM PROMPT
    # --------------------------------------------------------

    print("\n" + "═" * 70)
    print("PROMPT")
    print("═" * 70)

    print(
        "\nMặc định tool dùng system prompt dành cho dịch subtitle."
    )

    use_custom = confirm(
        "Muốn nhập prompt riêng?",
        False
    )

    system_prompt = DEFAULT_SYSTEM_PROMPT

    if use_custom:
        system_prompt = multiline_prompt(
            "NHẬP SYSTEM PROMPT TÙY CHỈNH",
            DEFAULT_SYSTEM_PROMPT
        )

    # --------------------------------------------------------
    # QUICK TEST
    # --------------------------------------------------------

    if confirm(
        "\nTest chat trước khi dịch?",
        True
    ):
        ok = quick_chat_test(
            base_url,
            api_key,
            model,
            system_prompt
        )

        if not ok:
            if not confirm(
                "\nChat test không lấy được content. "
                "Vẫn tiếp tục dịch?",
                False
            ):
                return

    # --------------------------------------------------------
    # SUMMARY
    # --------------------------------------------------------

    print("\n" + "═" * 70)
    print("XÁC NHẬN DỊCH")
    print("═" * 70)

    print(f"File       : {input_file.name}")
    print(f"Model      : {model}")
    print(f"From       : {source}")
    print(f"To         : {target}")
    print(f"Batch      : {batch_size}")
    print(f"Retry      : {retry_count}")
    print(f"Delay      : {delay}s")
    print(
        "Prompt     : "
        + ("CUSTOM" if use_custom else "DEFAULT")
    )

    if not confirm(
        "\nBắt đầu dịch?",
        True
    ):
        print("Đã hủy.")
        return

    try:
        translate_file(
            input_path=input_file,
            base_url=base_url,
            api_key=api_key,
            model=model,
            source=source,
            target=target,
            batch_size=batch_size,
            retry_count=retry_count,
            delay=delay,
            system_prompt=system_prompt,
        )

    except KeyboardInterrupt:
        print("\n\n⚠ Đã dừng bằng Ctrl+C.")
        print(
            "Batch đã hoàn thành vẫn nằm trong cache."
        )
        print(
            "Chạy lại để tiếp tục."
        )

    except Exception as e:
        print("\n❌ LỖI:")
        print(e)


if __name__ == "__main__":
    main()
