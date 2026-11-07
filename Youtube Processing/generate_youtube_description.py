import os
import json
from datetime import datetime
import subprocess
import pytz
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "Worship Scripts", "worship workflows"))
import service_keys  # noqa: E402

def get_mtime(filepath):
    try:
        cmd = ["git", "log", "-1", "--format=%ct", "--", filepath]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        out = result.stdout.strip()
        if out:
            return int(out)
    except Exception as e:
        pass
    return os.path.getmtime(filepath)

def format_date_for_ows(dt):
    return f"{dt.month}-{dt.day:02d}-{dt.strftime('%y')}"

def resolve_field_content(filepath, previous_sunday_ts, section_title):
    """Resolve a single service-titles file's content for chapter assembly.

    Returns "" if the file doesn't exist, is empty, or is older than the
    reset point for the current week (stale carry-over from a prior week).
    In all of those cases the caller treats the field as simply absent
    rather than failing the whole chapter line because of it.
    """
    if not os.path.exists(filepath):
        return ""

    if previous_sunday_ts is not None and get_mtime(filepath) < previous_sunday_ts:
        print(f"File {os.path.basename(filepath)} is older than previous Sunday. Treating as blank.")
        return ""

    with open(filepath, 'r') as f:
        content = f.read().strip()

    if section_title and content.lower().startswith(f"{section_title.lower()} - "):
        content = content[len(f"{section_title} - "):].strip()

    return content

def assemble_line(rest_of_line, txt_files, contents):
    """Fill in a template line's placeholders from resolved file contents.

    The first placeholder is the primary field (e.g. a piece's title) - if
    it's blank there's nothing worth showing for this chapter, so return
    None and let the caller drop the whole line. Any later placeholder
    (e.g. a credited performer/group) that is blank is dropped along with
    the separator joining it to the field before it, instead of
    invalidating the whole line - a missing optional credit shouldn't hide
    a piece we do have a title for.
    """
    if not txt_files:
        return rest_of_line

    primary = contents.get(txt_files[0], "")
    if not primary:
        return None

    resolved = rest_of_line.replace(txt_files[0], primary, 1)
    for txt_file in txt_files[1:]:
        content = contents.get(txt_file, "")
        if content:
            resolved = resolved.replace(txt_file, content, 1)
        else:
            resolved = re.sub(r'\s*[-,]\s*' + re.escape(txt_file), '', resolved, count=1)
    return resolved

def ows_slug_for(date, time=None):
    """The program link's slug: "11-01-26" as it has always been written, plus
    the OW index's time suffix for a timed service ("12-24-26-530pm")."""
    suffix = service_keys.ow_index_key(date, time)[len(service_keys.ow_index_key(date)):]
    return format_date_for_ows(date) + suffix

def render(template_lines, titles_dir, ows_slug, stale_before=None):
    """(boilerplate_lines, chapter_lines) for one service from its
    service-title files. stale_before: treat files last changed before this
    timestamp as blank (per-service folders are rewritten whole from their
    OW, so nothing in one can be a carry-over and they pass None)."""
    boilerplate_lines = []
    chapter_lines = []

    current_section_title = None
    current_section_performer = None

    for line in template_lines:
        if line.strip() == "{{PRELUDE_AUTO}}":
            # Prelude pieces are numbered prelude1, prelude2, ... with no
            # fixed maximum (see worship-prompt.txt) - piece 1 is handled by
            # the explicit template line above this marker, using the
            # top-level prelude-performer.txt. From piece 2 on, each piece N
            # pairs with prelude-performerN-1.txt (offset by one to account
            # for that unnumbered top-level performer credit). Keep going
            # until a preludeN.txt simply doesn't exist for this week.
            i = 2
            while True:
                piece_file = f"prelude{i}.txt"
                piece_path = os.path.join(titles_dir, piece_file)
                if not os.path.exists(piece_path):
                    break

                perf_file = f"prelude-performer{i - 1}.txt"
                perf_path = os.path.join(titles_dir, perf_file)

                contents = {
                    piece_file: resolve_field_content(piece_path, stale_before, None),
                    perf_file: resolve_field_content(perf_path, stale_before, None),
                }
                resolved = assemble_line(f"{piece_file} - {perf_file}", [piece_file, perf_file], contents)
                if resolved is not None:
                    chapter_lines.append(resolved)
                i += 1
            continue

        txt_files = re.findall(r'[\w-]+\.txt', line)
        if not txt_files:
            chapter_lines.append(line)
            continue

        is_boiler = "DescriptionBoiler.txt" in txt_files

        if is_boiler:
            line_valid = True
            modified_line = line
            for txt_file in txt_files:
                filepath = txt_file if txt_file == "DescriptionBoiler.txt" else os.path.join(titles_dir, txt_file)
                if not os.path.exists(filepath):
                    line_valid = False
                    break
                with open(filepath, 'r') as f:
                    content = f.read().strip()
                if not content:
                    line_valid = False
                    break

                content = re.sub(
                    r'(https://www\.fccla\.org/ows/)(?:\[DATE OF SERVICE\]|[\w-]+)',
                    rf'\g<1>{ows_slug}',
                    content
                )
                modified_line = modified_line.replace(txt_file, content)

            if line_valid:
                boilerplate_lines.append(modified_line)
            continue

        # Process Chapter Lines
        # Extract potential section title
        m = re.match(r'^([^,]+),\s*(.*\.txt.*)$', line)
        if m and not re.search(r'\.txt', m.group(1)):
            section_title = m.group(1).strip()
            rest_of_line = m.group(2).strip()
        else:
            section_title = None
            rest_of_line = line.strip()

        is_explicit_numbered_piece = bool(re.search(r'\d+\.txt', line))

        max_index = 0
        has_numbered = False

        if not is_explicit_numbered_piece:
            for txt_file in txt_files:
                base_name = txt_file[:-4]
                idx = 1
                while os.path.exists(os.path.join(titles_dir, f"{base_name}{idx}.txt")):
                    idx += 1
                if idx > 1:
                    max_index = max(max_index, idx - 1)
                    has_numbered = True

        if has_numbered:
            for i in range(0, max_index + 1):
                contents = {}
                for txt_file in txt_files:
                    base_name = txt_file[:-4]
                    if i == 0:
                        num_filepath = os.path.join(titles_dir, txt_file)
                    else:
                        num_filepath = os.path.join(titles_dir, f"{base_name}{i}.txt")
                        if not os.path.exists(num_filepath):
                            num_filepath = os.path.join(titles_dir, txt_file)

                    contents[txt_file] = resolve_field_content(num_filepath, stale_before, section_title)

                resolved_rest = assemble_line(rest_of_line, txt_files, contents)
                if resolved_rest is None:
                    continue

                if i == 0 and section_title:
                    chapter_lines.append(f"{section_title} - {resolved_rest}")
                else:
                    chapter_lines.append(resolved_rest)
        else:
            contents = {}
            for txt_file in txt_files:
                filepath = os.path.join(titles_dir, txt_file)
                contents[txt_file] = resolve_field_content(filepath, stale_before, section_title)

            resolved_rest = assemble_line(rest_of_line, txt_files, contents)
            if resolved_rest is not None:
                if section_title:
                    chapter_lines.append(f"{section_title} - {resolved_rest}")
                else:
                    chapter_lines.append(resolved_rest)

    return boilerplate_lines, chapter_lines

def write_if_changed(path, text):
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except FileNotFoundError:
        pass
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    return True

def main():
    tz = pytz.timezone('America/Los_Angeles')
    now = datetime.now(tz)
    force = str(os.environ.get("FORCE_UPDATE", "false")).lower() == "true"

    template_path = "DescriptionTemplate.txt"
    if not os.path.exists(template_path):
        print(f"Template not found at {template_path}")
        return
    with open(template_path, "r") as f:
        template_lines = f.read().splitlines()

    # Each service's Description.txt and chapters.txt are rendered from its own
    # service-title folder (Worship Scripts/services/<key>/, written by
    # update_service_titles.py as soon as its OW is posted -- weeks ahead is
    # fine). Services already over are left as they were.
    for key, date, time in service_keys.list_services():
        if service_keys.service_start(date, time) + service_keys.LIVE_WINDOW < now:
            continue
        state = service_keys.read_state(key)
        folder = service_keys.service_dir(key)
        try:
            boilerplate_lines, chapter_lines = render(
                template_lines, service_keys.titles_dir(key), ows_slug_for(date, time))
        except Exception as e:
            print(f"Error rendering {key}: {e}")
            continue

        changed = write_if_changed(os.path.join(folder, "Description.txt"), "\n".join(boilerplate_lines))
        changed |= write_if_changed(os.path.join(folder, "chapters.txt"), "\n".join(chapter_lines))
        # Record which OW this description came from: service_keys.content_ready()
        # only trusts it when that matches the titles' OW.
        desc_state = service_keys.read_description_state(key) or {}
        if changed or force or desc_state.get("ow_sha") != state.get("ow_sha"):
            service_keys.write_description_state(key, {"ow_sha": state.get("ow_sha")})
            print(f"Saved Description.txt and chapters.txt for {key}.")

    # The live Description.txt / chapters.txt (the booth's OSC server reads
    # chapters.txt for its chapter list) follow whichever service
    # service-titles/ holds -- update_service_titles.py decides that, and
    # doesn't switch mid-stream.
    key = service_keys.live_key()
    if not key:
        print("service-titles/ doesn't name a generated service yet; leaving the live Description.txt as it is.")
        return
    folder = service_keys.service_dir(key)
    for name in ("Description.txt", "chapters.txt"):
        src = os.path.join(folder, name)
        if os.path.exists(src):
            with open(src, encoding="utf-8") as f:
                if write_if_changed(name, f.read()):
                    print(f"Live {name} is now {key}'s.")
    date, time = service_keys.parse_service_key(key)
    # target_date mirrors service_titles_state.json's (see update_service_titles.py).
    write_if_changed("description_state.json",
                     json.dumps({"target_date": date.isoformat(), "target_time": time, "key": key}))

if __name__ == '__main__':
    main()
