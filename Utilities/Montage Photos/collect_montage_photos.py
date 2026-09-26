"""
Collects photos from The Meetinghouse into Drive for the annual montage.

Every year someone has to put together a video of everything the church did,
and the best record of that is the newsletter: most weeks it runs photos of
last Sunday, the gardens, the food distribution, the kids. This reads each
issue as it goes out, keeps the real photographs, and files them into the
montage folder by what they show, so the montage starts from a sorted pile
rather than a year of emails.

The chain:
    Constant Contact's archive API   every issue, as JSON
      -> each issue not yet processed, oldest first
      -> the photos in it (not spacers, not ones already seen)
      -> Gemini looks at them in place in the issue's text and decides
         keep/skip, category and subcategory
      -> the original file, uploaded to <category>/<subcategory>/ in Drive

Categories are the montage folder's top-level folders, read from Drive on
every run, so adding a folder there is how a new category is made. Anything
that fits none of them goes to OTHER_CATEGORY. Subcategories are made as
needed -- Worship/Easter, Events/Pride -- and Gemini is shown the ones that
already exist so it files the next Easter photo in the same place.

Newsletters repeat themselves: the same banner every week, the same Food at
First photo for a month. Every image is keyed by a hash of its bytes, and a
hash seen before is never asked about or uploaded again. Uploads also carry
that hash in Drive's appProperties, so a run whose state never got committed
finds its own uploads rather than making duplicates.

An issue is marked done only once every photo in it has been decided and
uploaded. Anything that fails leaves the issue to be retried next run; what
did succeed is recorded, so the retry only redoes the rest.

Each montage runs fiscal year to fiscal year, so the folder and the first
issue to take are settings (MONTAGE_FOLDER_ID, MONTAGE_SINCE) to change when
next year's folder is made.

Env:
    GEMINI_API_KEY   required
    GDRIVE_OAUTH_JSON / GDRIVE_SERVICE_ACCOUNT_JSON   required, except in a dry run
    MONTAGE_FOLDER_ID   overrides DEFAULT_FOLDER_ID
    MONTAGE_SINCE    YYYY-MM-DD; issues before this are ignored
    MAX_ISSUES       issues to process per run (default MAX_ISSUES_PER_RUN)
    DRY_RUN          "1" asks Gemini and prints the plan, but uploads and saves nothing
"""

import datetime
import hashlib
import html as html_mod
import io
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
STATE_PATH = os.path.join(HERE, 'montage_photos_state.json')

REPO_ROOT = os.path.abspath(os.path.join(HERE, os.pardir, os.pardir))
NEWSLETTER_DIR = os.path.join(REPO_ROOT, 'Utilities', 'Newsletter Ad')
UPLOADER_DIR = os.path.join(REPO_ROOT, 'Worship Scripts', 'worship workflows')
for _path in (NEWSLETTER_DIR, UPLOADER_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

# The newsletter card already knows how to read an issue; one reader keeps
# the two from disagreeing about what an issue says.
from generate_newsletter_ad import (  # noqa: E402
    USER_AGENT,
    clip,
    fetch_issue,
    issue_text,
    parse_title_date,
)

# "2027 Assets": the montage shown at the 2027 annual meeting, which falls
# around the end of the fiscal year in July. Next year's folder replaces this.
DEFAULT_FOLDER_ID = '1RUhLoeCXhrB0Q3usnzTNo9B13xhhnxwP'
DEFAULT_SINCE = '2026-05-01'

# The JSON the fccla.org archive widget reads. The page itself only shows ten
# issues; this goes back years, which is what makes a backfill possible.
# data-m on the page's #archiveList is the account id.
ARCHIVE_API = ('https://campaignlp.constantcontact.com/v1/archive/'
               'a07e2ibuhn30/activities?limit=100')

GEMINI_MODEL = 'gemini-3.5-flash'

# Each issue is ~20 images; a backfill of a year is ~50 issues. Spreading it
# over a few runs keeps any one run short and gets its state committed
# before the next starts.
MAX_ISSUES_PER_RUN = 5

OTHER_CATEGORY = 'Other'

# What the folder names mean, for Gemini. Only names that actually exist in
# Drive are offered, so a hint for a folder that's been removed is harmless,
# and a new folder with no hint still works on its name alone.
CATEGORY_HINTS = {
    'worship': 'Sunday services and special services: sanctuary, choir, '
               'ministers, communion, baptisms, holiday services.',
    'events': 'Gatherings outside worship: concerts, talks, fellowship '
              'meals, retreats, parties, community events, marches.',
    'first kids firsts': "First Kids First, the children's and youth "
                         'program: kids and youth activities of any kind.',
    'food at first': 'The Food at First distribution: groceries, clothing '
                     'drives, volunteers packing and handing out food.',
    'gardens': 'The community gardens and urban farm: planting, harvesting, '
               'workdays, produce in the beds.',
}

# The content images Constant Contact lays out. Everything else in an issue
# is a 1px spacer, a social icon or a tracking pixel.
IMAGE_HOST = 'files.constantcontact.com'
# Below this display width an image is an icon or a signature, not a photo.
MIN_DISPLAY_WIDTH = 120

# Gemini gets a smaller copy: it decides just as well from 1280px, and a
# year-old issue's two dozen full-size PNGs would crowd the request limit.
PREVIEW_MAX_SIDE = 1280

EXTENSIONS = {'image/jpeg': '.jpg', 'image/png': '.png', 'image/gif': '.gif',
              'image/webp': '.webp'}


# --------------------------------------------------------------------------
# The archive
# --------------------------------------------------------------------------

def fetch_archive(timeout=60):
    req = urllib.request.Request(ARCHIVE_API, headers={
        'User-Agent': USER_AGENT, 'Origin': 'https://www.fccla.org'})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode('utf-8'))


def issues_to_process(archive, since, done):
    """Issues on or after `since` that haven't been done, oldest first.

    Oldest first so a subfolder is named by the first issue that needs it
    and later issues file into it, and so a backfill fills the year in order.
    Issues without a date in the subject can't be placed in the year and are
    left out.
    """
    issues = []
    for row in archive or []:
        url = (row or {}).get('campaignUrl')
        title = re.sub(r'\s+', ' ', str((row or {}).get('subject') or '')).strip()
        date = parse_title_date(title)
        if not url or not date or date < since or url in done:
            continue
        issues.append({'url': url, 'title': title, 'date': date})
    issues.sort(key=lambda e: e['date'])
    return issues


# --------------------------------------------------------------------------
# An issue's photos
# --------------------------------------------------------------------------

def image_url(tag):
    m = re.search(r'\ssrc="([^"]+)"', tag)
    return html_mod.unescape(m.group(1)) if m else ''


def is_content_image(tag):
    url = image_url(tag)
    if IMAGE_HOST not in url:
        return False
    m = re.search(r'\swidth="(\d+)"', tag)
    return not (m and int(m.group(1)) < MIN_DISPLAY_WIDTH)


def mark_images(raw_html):
    """The issue with each photo replaced by an [[IMAGE n]] marker, and the
    photos' URLs in marker order.

    Constant Contact images have no alt text, so the only thing that says
    what a photo is is the copy around it: "Last Sunday, First Kids First
    gathered in our Gardens" sits right above the picture of it. Keeping the
    markers in the text lets Gemini read each photo in that place.
    """
    urls = []

    def replace(m):
        tag = m.group(0)
        if not is_content_image(tag):
            return tag
        url = image_url(tag)
        if url in urls:
            n = urls.index(url) + 1
        else:
            urls.append(url)
            n = len(urls)
        return f'<div>[[IMAGE {n}]]</div>'

    marked = re.sub(r'(?is)<img\b[^>]*>', replace, raw_html)
    return marked, urls


def download(url, timeout=60):
    req = urllib.request.Request(url, headers={'User-Agent': USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
        mime = (resp.headers.get('Content-Type') or '').split(';')[0].strip()
    if not mime.startswith('image/'):
        mime = guess_mime(url)
    return data, mime


def guess_mime(url):
    path = url.split('?')[0].lower()
    for mime, ext in EXTENSIONS.items():
        if path.endswith(ext) or (ext == '.jpg' and path.endswith('.jpeg')):
            return mime
    return 'image/jpeg'


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def preview(data, mime):
    """A JPEG no bigger than PREVIEW_MAX_SIDE, for Gemini; the original if
    Pillow can't read it."""
    try:
        from PIL import Image
        img = Image.open(io.BytesIO(data))
        img.thumbnail((PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE))
        if img.mode != 'RGB':
            img = img.convert('RGB')
        out = io.BytesIO()
        img.save(out, 'JPEG', quality=85)
        return out.getvalue(), 'image/jpeg'
    except Exception:                             # noqa: BLE001
        return data, mime


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------

def describe_categories(tree):
    lines = []
    for name in sorted(tree, key=str.lower):
        hint = CATEGORY_HINTS.get(name.lower(), '')
        subs = sorted(tree[name]['subfolders'], key=str.lower)
        line = f'  - "{name}"' + (f': {hint}' if hint else '')
        if subs:
            line += '\n      existing subcategories: ' + ', '.join(f'"{s}"' for s in subs)
        lines.append(line)
    return '\n'.join(lines)


def build_prompt(issue, text, numbers, tree):
    listing = ', '.join(f'IMAGE {n}' for n in numbers)
    return f"""This is one issue of a church's weekly email newsletter, "The Meetinghouse", sent {issue['date']:%B} {issue['date'].day}, {issue['date'].year}. Where each picture appears in the newsletter, the text has a marker like [[IMAGE 3]]; the pictures themselves follow, each labelled the same way.

Every year the church makes a montage video of everything it did. Decide which of these pictures belong in it, and where each one should be filed: {listing}.

Keep a picture only if it is a real photograph of this church's life: people at worship, events, programs, volunteers, the gardens, the building in use. Skip everything else: flyers, posters, graphics, logos, banners, anything that is mostly text, clip art, stock photos, book covers, and posed single-person headshots of speakers or staff. When a photograph is borderline, keep it -- a person will go through the folder before the montage is made.

Categories (use one of these names exactly):
{describe_categories(tree)}
  - "{OTHER_CATEGORY}": only if a kept photo fits none of the above.

When a photo could fit more than one, choose by who or what it is mainly about: children and youth doing anything go in the children's program category; a food distribution goes in the food distribution category even if the produce came from the gardens.

A subcategory is the specific occasion, holiday or named event a photo is from -- "Easter", "Christmas Eve", "Pride", "Blessing of the Animals", "Summer Camp", "Saturday Workday". If the category already has a subcategory for it, use that name exactly. Otherwise make one: 1 to 4 words, Title Case, the name people at the church would use, no dates or years. Leave it empty for a photo from an ordinary week that isn't tied to any particular occasion.

For each picture give:
  "image": its number
  "keep": true or false
  "category": the category name, or "" if not kept
  "subcategory": as above, or ""
  "description": 3 to 8 words saying what the photo shows, for its file name -- e.g. "Choir singing at sunrise service". "" if not kept.

Newsletter:
{text[:40000]}

Respond with ONLY compact JSON, no markdown fences, no commentary, matching exactly this schema:
{{"images": [{{"image": 1, "keep": true, "category": "Worship", "subcategory": "Easter", "description": "Choir singing at sunrise service"}}]}}"""


def ask_gemini(issue, text, previews, tree, api_key):
    """{image number: decision} for `previews` ({number: (bytes, mime)}).

    Raises on anything unexpected: an issue Gemini couldn't read is retried
    next run rather than recorded as having no photos.
    """
    from google import genai
    from google.genai import types

    numbers = sorted(previews)
    contents = [build_prompt(issue, text, numbers, tree)]
    for n in numbers:
        data, mime = previews[n]
        contents.append(f'IMAGE {n}:')
        contents.append(types.Part.from_bytes(data=data, mime_type=mime))

    client = genai.Client(api_key=api_key,
                          http_options=types.HttpOptions(timeout=600000))
    resp = client.models.generate_content(
        model=GEMINI_MODEL, contents=contents,
        config=types.GenerateContentConfig(response_mime_type='application/json'))
    return parse_decisions(resp.text, numbers)


def parse_decisions(raw, numbers):
    raw = re.sub(r'^```(?:json)?|```$', '', (raw or '').strip(), flags=re.M).strip()
    parsed = json.loads(raw)
    decisions = {}
    for row in parsed.get('images') or []:
        if not isinstance(row, dict):
            continue
        try:
            n = int(row.get('image'))
        except (TypeError, ValueError):
            continue
        if n not in numbers:
            continue
        keep = row.get('keep') is True or str(row.get('keep')).lower() == 'true'
        decisions[n] = {
            'keep': keep,
            'category': clean_name(row.get('category')) if keep else '',
            'subcategory': clean_name(row.get('subcategory')) if keep else '',
            'description': clean_name(row.get('description'), 80) if keep else '',
        }
    missing = [n for n in numbers if n not in decisions]
    if missing:
        raise ValueError(f'Gemini gave no decision for image(s) {missing}')
    return decisions


def clean_name(value, limit=40):
    """Safe as a Drive folder or file name: no slashes, no runs of spaces,
    no trailing punctuation, clipped on a word boundary."""
    text = re.sub(r'[\\/:*?"<>|\x00-\x1f]+', ' ', str(value or ''))
    text = re.sub(r'\s+', ' ', text).strip().strip('.')
    return clip(text, limit).rstrip('…').strip()


# --------------------------------------------------------------------------
# Filing
# --------------------------------------------------------------------------

def name_key(name):
    """Two folder names that should count as one: "Christmas Eve" and
    "christmas-eve", and "First Kids First" -- the program's name, which is
    what Gemini will say -- and "First Kids Firsts", the folder's."""
    words = re.findall(r'[a-z0-9]+', (name or '').lower())
    return ''.join(w[:-1] if len(w) > 3 and w.endswith('s') else w for w in words)


def match_name(name, existing):
    key = name_key(name)
    if not key:
        return None
    return next((e for e in existing if name_key(e) == key), None)


def resolve_folder(decision, tree):
    """(category, subcategory) as they are, or will be, named in Drive."""
    category = (match_name(decision['category'], tree)
                or match_name(OTHER_CATEGORY, tree) or OTHER_CATEGORY)
    sub = decision['subcategory']
    if sub:
        existing = tree.get(category, {}).get('subfolders', {})
        sub = match_name(sub, existing) or sub
    return category, sub


def file_name(issue, decision, mime, n):
    desc = decision['description'] or f'Photo {n}'
    return f"{issue['date'].isoformat()} {desc}{EXTENSIONS.get(mime, '.jpg')}"


# --------------------------------------------------------------------------
# Drive
# --------------------------------------------------------------------------

FOLDER_MIME = 'application/vnd.google-apps.folder'


def list_folders(drive, parent_id):
    folders, token = {}, None
    while True:
        resp = drive.files().list(
            q=f"'{parent_id}' in parents and mimeType='{FOLDER_MIME}' and trashed=false",
            fields='nextPageToken, files(id, name)', pageToken=token,
            supportsAllDrives=True, includeItemsFromAllDrives=True).execute()
        for f in resp.get('files', []):
            folders.setdefault(f['name'], f['id'])
        token = resp.get('nextPageToken')
        if not token:
            return folders


def load_tree(drive, root_id):
    """{category: {'id', 'subfolders': {name: id}}} for the montage folder."""
    return {name: {'id': fid, 'subfolders': list_folders(drive, fid)}
            for name, fid in list_folders(drive, root_id).items()}


def check_access(drive, root_id):
    meta = drive.files().get(fileId=root_id, supportsAllDrives=True,
                             fields='name, capabilities(canAddChildren)').execute()
    if not (meta.get('capabilities') or {}).get('canAddChildren'):
        who = drive.about().get(fields='user(emailAddress)').execute()
        raise RuntimeError(
            f"{(who.get('user') or {}).get('emailAddress')} can't add files to "
            f"{meta.get('name')!r}. Share the folder with that account as an editor.")
    return meta.get('name')


def make_folder(drive, parent_id, name):
    return drive.files().create(
        body={'name': name, 'mimeType': FOLDER_MIME, 'parents': [parent_id]},
        fields='id', supportsAllDrives=True).execute()['id']


def ensure_folder(drive, tree, root_id, category, sub):
    """The Drive folder id for category/sub, making either if needed."""
    if category not in tree:
        print(f'    Making folder {category}/')
        tree[category] = {'id': make_folder(drive, root_id, category), 'subfolders': {}}
    node = tree[category]
    if not sub:
        return node['id']
    if sub not in node['subfolders']:
        print(f'    Making folder {category}/{sub}/')
        node['subfolders'][sub] = make_folder(drive, node['id'], sub)
    return node['subfolders'][sub]


def find_uploaded(drive, digest):
    resp = drive.files().list(
        q=f"appProperties has {{ key='montageSha' and value='{digest}' }} and trashed=false",
        fields='files(id)', supportsAllDrives=True,
        includeItemsFromAllDrives=True).execute()
    files = resp.get('files', [])
    return files[0]['id'] if files else None


def upload(drive, folder_id, name, data, mime, digest, description):
    from googleapiclient.http import MediaIoBaseUpload
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=mime, resumable=False)
    body = {'name': name, 'parents': [folder_id], 'description': description,
            'appProperties': {'montageSha': digest}}
    return drive.files().create(body=body, media_body=media, fields='id',
                                supportsAllDrives=True).execute()['id']


def get_drive():
    from upload_queue_to_drive import get_drive_service
    return get_drive_service()


# --------------------------------------------------------------------------
# State
# --------------------------------------------------------------------------

def load_state():
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    state.setdefault('issues', {})
    state.setdefault('images', {})
    return state


def save_state(state):
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write('\n')


# --------------------------------------------------------------------------

def process_issue(issue, state, tree, drive, root_id, api_key, dry_run):
    """Decide and file one issue's photos. True when the issue is finished.

    A dry run records its decisions in `state` like a real one -- so later
    issues in the same run skip what it has already seen -- and main() just
    doesn't save them.
    """
    print(f"\n{issue['title']}")
    marked, urls = mark_images(fetch_issue(issue['url']))
    text = issue_text(marked)

    # Download everything first: the hash, not the URL, is what says whether
    # a photo has been seen, and the same photo is re-uploaded under a new
    # URL when it's reused in a later issue.
    fresh = {}
    for n, url in enumerate(urls, start=1):
        data, mime = download(url)
        digest = sha256(data)
        if digest in state['images'] or any(f['sha'] == digest for f in fresh.values()):
            continue
        fresh[n] = {'url': url, 'data': data, 'mime': mime, 'sha': digest}
    print(f'  {len(urls)} image(s), {len(fresh)} not seen before.')

    decisions = {}
    if fresh:
        previews = {n: preview(f['data'], f['mime']) for n, f in fresh.items()}
        decisions = ask_gemini(issue, text, previews, tree, api_key)

    ok = True
    for n, f in fresh.items():
        decision = decisions[n]
        record = {'issue': issue['url'], 'source': f['url'], 'keep': decision['keep']}
        if not decision['keep']:
            print(f'  IMAGE {n}: skip')
            state['images'][f['sha']] = record
            continue

        category, sub = resolve_folder(decision, tree)
        name = file_name(issue, decision, f['mime'], n)
        where = f'{category}/{sub + "/" if sub else ""}{name}'
        print(f'  IMAGE {n}: {where}')
        record.update({'category': category, 'subcategory': sub, 'name': name})
        if dry_run:
            # Pretend the folder exists so later photos in this dry run are
            # shown filing into it, as they would for real.
            node = tree.setdefault(category, {'id': None, 'subfolders': {}})
            if sub:
                node['subfolders'].setdefault(sub, None)
            state['images'][f['sha']] = record
            continue
        try:
            drive_id = find_uploaded(drive, f['sha'])
            if drive_id:
                print('    Already in Drive; not uploading again.')
            else:
                folder_id = ensure_folder(drive, tree, root_id, category, sub)
                description = (f"{decision['description']}\n\nFrom {issue['title']}\n"
                               f"{issue['url']}")
                drive_id = upload(drive, folder_id, name, f['data'], f['mime'],
                                  f['sha'], description)
        except Exception as exc:                  # noqa: BLE001
            print(f'    Upload failed ({exc}); will retry next run.')
            ok = False
            continue
        record['drive_id'] = drive_id
        state['images'][f['sha']] = record

    if ok:
        state['issues'][issue['url']] = {
            'title': issue['title'], 'date': issue['date'].isoformat(),
            'kept': sum(1 for d in decisions.values() if d['keep'])}
    return ok


def main():
    dry_run = os.environ.get('DRY_RUN') == '1'
    root_id = os.environ.get('MONTAGE_FOLDER_ID') or DEFAULT_FOLDER_ID
    since = datetime.date.fromisoformat(os.environ.get('MONTAGE_SINCE') or DEFAULT_SINCE)
    max_issues = int(os.environ.get('MAX_ISSUES') or MAX_ISSUES_PER_RUN)
    api_key = os.environ.get('GEMINI_API_KEY')
    if not api_key:
        sys.exit('GEMINI_API_KEY is not set.')

    drive = get_drive()
    if drive:
        print(f'Montage folder: {check_access(drive, root_id)}')
        tree = load_tree(drive, root_id)
    elif dry_run:
        print('No Drive credentials; dry run with the categories as last seen.')
        tree = {name: {'id': None, 'subfolders': {}} for name in
                ('Events', 'First Kids Firsts', 'Food at First', 'Gardens', 'Worship')}
    else:
        sys.exit('No Drive credentials: set GDRIVE_OAUTH_JSON or GDRIVE_SERVICE_ACCOUNT_JSON.')
    print('Categories: ' + ', '.join(sorted(tree)))

    state = load_state()
    pending = issues_to_process(fetch_archive(), since, state['issues'])
    print(f'{len(pending)} issue(s) since {since} to process; doing up to {max_issues}.')

    failed = []
    for issue in pending[:max_issues]:
        try:
            done = process_issue(issue, state, tree, drive, root_id, api_key, dry_run)
        except Exception as exc:                  # noqa: BLE001
            print(f'  Failed ({exc.__class__.__name__}: {exc}); will retry next run.')
            done = False
        if not done:
            failed.append(issue['title'])
        if not dry_run:
            # After every issue, so a run that dies partway keeps what it did.
            save_state(state)

    if failed:
        sys.exit(f'{len(failed)} issue(s) not finished: ' + '; '.join(failed))
    print('\nDone.')


if __name__ == '__main__':
    main()
