// UpdateWeek.jsx - builds this week's Service Title + Sermon Title graphics from last week's .ai files.
// Run in Illustrator: File > Scripts > Other Script...
//
// Everything that changes week to week lives in week-data.txt (next to this script), written by
// prepare_week.py / Gemini from the Order of Worship PDF. This file should not need edits week to
// week; see README.md and GEMINI.md.
#target illustrator

var MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December";
var REQUIRED = ["root", "heading", "dateText", "preacher", "panelColor", "imagePath",
    "outputFolder", "serviceTemplate", "serviceFolder", "serviceName", "sermonTemplate", "sermonFolder", "sermonName"];
var OPTIONAL = ["timeZone", "photoShiftX", "photoShiftY"];
var LEGACY = ["series", "seriesName", "title"];         // the heading as three fields, before "heading"

var log = [], problems = [], relock = [];
function L(s) { log.push(s); }
function P(s) { log.push("  !! " + s); problems.push(s); }
function strip(s) { return s.replace(/^\s+|\s+$/g, ""); }
function center(b) { return [(b[0] + b[2]) / 2, (b[1] + b[3]) / 2]; } // b = [left, top, right, bottom]
function fmt(b) { return b.join(",").replace(/(\.\d)\d+/g, "$1"); }
function num(s) { var n = parseFloat(s); return isNaN(n) ? 0 : n; }
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function has(list, s) { for (var i = 0; i < list.length; i++) if (list[i] == s) return true; return false; }

function hexColor(h) {
    var c = new RGBColor();
    c.red = parseInt(h.substr(1, 2), 16); c.green = parseInt(h.substr(3, 2), 16); c.blue = parseInt(h.substr(5, 2), 16);
    return c;
}
function sameRGB(a, b) {
    return a && b && a.typename == "RGBColor" && b.typename == "RGBColor" &&
        Math.abs(a.red - b.red) < 1.5 && Math.abs(a.green - b.green) < 1.5 && Math.abs(a.blue - b.blue) < 1.5;
}
function rgbText(c) { return "rgb(" + Math.round(c.red) + "," + Math.round(c.green) + "," + Math.round(c.blue) + ")"; }

// ---------- week-data.txt ----------

function readData(f) {
    f.encoding = "UTF-8";
    if (!f.open("r")) throw new Error("can't open " + f.fsName);
    var txt = f.read(); f.close();
    var d = {}, lines = txt.replace(/^﻿/, "").split(/\r\n|\r|\n/);
    for (var i = 0; i < lines.length; i++) {
        var s = strip(lines[i]), eq = s.indexOf("=");
        if (s == "" || s.charAt(0) == "#" || eq < 1) continue;
        d[strip(s.substr(0, eq))] = strip(s.substr(eq + 1));   // values may contain '='; only the first splits
    }
    return d;
}

function validate(d) {
    var bad = [], k;
    for (var i = 0; i < REQUIRED.length; i++)
        if (!d[REQUIRED[i]] && !(REQUIRED[i] == "heading" && headingOf(d).length)) bad.push("missing " + REQUIRED[i]);
    for (k in d) if (!has(REQUIRED, k) && !has(OPTIONAL, k) && !has(LEGACY, k)) bad.push("unknown key " + k + " (typo?)");
    if (d.panelColor && !/^#[0-9A-Fa-f]{6}$/.test(d.panelColor)) bad.push("panelColor must look like #325673, not " + d.panelColor);
    if (d.dateText && !new RegExp("^(" + MONTHS + ") \\d{1,2}, \\d{4}$").test(d.dateText))
        bad.push("dateText must look like September 27, 2026, not " + d.dateText);
    if (d.timeZone && d.timeZone != "PDT" && d.timeZone != "PST") bad.push("timeZone must be PDT or PST");
    if (d.photoShiftX && isNaN(parseFloat(d.photoShiftX))) bad.push("photoShiftX must be a number");
    if (d.photoShiftY && isNaN(parseFloat(d.photoShiftY))) bad.push("photoShiftY must be a number");
    return bad;
}

// "~/..." and "/..." are absolute; anything else is relative to base.
function resolvePath(p, base) {
    if (p.substr(0, 2) == "~/") return Folder("~").fsName + p.substr(1);
    if (p.charAt(0) == "/") return p;
    return base + "/" + p;
}

// Last week's folder may have moved into (or out of) Past Weeks, and older sermon files are
// named SermonSeries_M-D-YY.ai; try those before giving up.
function findTemplate(p, root) {
    var tries = [p];
    if (p.indexOf(root + "/") == 0) {
        var rel = p.substr(root.length + 1);
        tries.push(rel.indexOf("Past Weeks/") == 0 ? root + "/" + rel.substr(11) : root + "/Past Weeks/" + rel);
    }
    for (var i = 0, n = tries.length; i < n; i++) {
        if (/\/Sermon Title_[^\/]*$/.test(tries[i])) tries.push(tries[i].replace(/\/Sermon Title_([^\/]*)$/, "/SermonSeries_$1"));
        else if (/\/SermonSeries_[^\/]*$/.test(tries[i])) tries.push(tries[i].replace(/\/SermonSeries_([^\/]*)$/, "/Sermon Title_$1"));
    }
    for (var j = 0; j < tries.length; j++) if (new File(tries[j]).exists) return tries[j];
    return null;
}

function isOpen(path) {
    var want = new File(path).fsName;
    for (var i = 0; i < app.documents.length; i++) {
        try { if (app.documents[i].fullName.fsName == want) return true; } catch (e) {}   // untitled docs have no file
    }
    return false;
}

// ---------- document helpers ----------

function unlockAll(doc) {
    var saved = [];
    for (var i = 0; i < doc.layers.length; i++) { saved.push(doc.layers[i].locked); doc.layers[i].locked = false; }
    return function () { for (var i = 0; i < doc.layers.length; i++) doc.layers[i].locked = saved[i]; };
}
function unlock(it) {
    try { if (it.locked) { it.locked = false; relock.push(it); } } catch (e) {}
}

function isHidden(it) {
    try {
        for (var p = it; p && p.typename != "Document"; p = p.parent) {
            if (p.typename == "Layer") { if (!p.visible) return true; }
            else if (p.hidden) return true;
        }
    } catch (e) {}
    return false;
}

// Replace len characters at idx without touching the formatting of the others: the first old
// character becomes the new text (inheriting its style), the rest are removed. Setting
// textFrame.contents instead would flatten a multi-line frame to one style.
function replaceAt(tf, idx, len, newStr) {
    if (len <= 0 || tf.contents.substr(idx, len) == newStr) return;
    unlock(tf);
    tf.characters[idx].contents = newStr;
    for (var k = 1; k < len; k++) tf.characters[idx + newStr.length].remove();
}

// Lines (paragraphs) of a frame with their character offsets. A forced line break (\u0003), used to
// split a long line in two, stays inside its line.
function splitLines(s) {
    var out = [], start = 0;
    for (var i = 0; i <= s.length; i++) {
        var ch = i < s.length ? s.charAt(i) : "\r";
        if (ch == "\r" || ch == "\n") { out.push({ start: start, text: s.substring(start, i) }); start = i + 1; }
    }
    while (out.length && strip(out[out.length - 1].text) == "") out.pop();   // the title frame ends in an empty line
    return out;
}
function span(line) {
    var t = strip(line.text);
    return { start: line.start + line.text.indexOf(t), len: t.length, text: t };
}

// Scale the type size of a range, and its line spacing with it: the heading's leading is fixed (e.g.
// 74pt type on 114pt leading), so shrinking only the type would leave the lines spread apart. The top
// line keeps its leading (keepLeading): that sets where its baseline sits, and the top line stays put.
function scaleChars(tf, start, len, k, keepLeading) {
    for (var i = start; i < start + len; i++) {
        var a = tf.characters[i].characterAttributes;
        a.size = a.size * k;
        if (!a.autoLeading && !keepLeading) a.leading = a.leading * k;
    }
}

// New text inherits the size of the text it replaces, so a shrink made to fit a long line would
// carry into next week's short one. Each shrink is recorded in the frame's note (Attributes panel),
// per line ("UpdateWeek shrink title 0.8000"), and undone before the next week's text is fitted.
function shrinkRe(label) { return new RegExp("\\s*UpdateWeek shrink " + label + " ([\\d.]+)"); }
function unshrink(tf, start, len, label, keepLeading) {
    var re = shrinkRe(label), m = re.exec(tf.note || "");
    if (!m) return;
    scaleChars(tf, start, len, 1 / parseFloat(m[1]), keepLeading);
    tf.note = tf.note.replace(re, "");
}
function markShrink(tf, k, label) { tf.note = (tf.note || "").replace(shrinkRe(label), "") + " UpdateWeek shrink " + label + " " + k.toFixed(4); }

// Shrink point text that grew wider than maxW (keeps it centered on its own anchor).
function fitWidth(tf, maxW) {
    if (tf.kind != TextType.POINTTEXT) return;
    unshrink(tf, 0, tf.characters.length, "line");
    var w = tf.width;
    if (w > maxW) {
        var k = maxW / w;
        scaleChars(tf, 0, tf.characters.length, k);
        markShrink(tf, k, "line");
        L("  shrank '" + strip(tf.contents).substr(0, 30) + "' to fit (" + Math.round(k * 100) + "%)");
    }
}

// Area text wraps instead of growing, and a line that wraps pushes the lines below it down and out
// of the box. So each changed line is fitted on its own, top to bottom: shrink just that line
// until it takes no more lines than it did last week (at least one) and isn't pushed out.
// Illustrator's paragraphs collection starts a new "paragraph" at a forced line break (\u0003) as well
// as at a real one, so a line split in two is two of them; count the same way.
function paraOf(tf, offset) {
    var c = tf.contents, n = 0;
    for (var i = 0; i < offset; i++) if (c.charAt(i) == "\r" || c.charAt(i) == "\u0003") n++;
    return n;
}
function linesOfRange(tf, start, len) {                    // visual lines of each part of a line
    var out = [];
    for (var p = paraOf(tf, start); p <= paraOf(tf, start + Math.max(len, 1) - 1); p++) out.push(paraLines(tf, p));
    return out;
}
function anyHidden(counts) { for (var i = 0; i < counts.length; i++) if (counts[i] == 0) return true; return false; }
function paraLines(tf, p) { try { return tf.paragraphs[p].lines.length; } catch (e) { return -1; } }   // 0 = pushed out
// The space nearest the middle of a line, to break it into two halves of about the same length.
function balancedBreak(text) {
    var best = -1;
    for (var i = 1; i < text.length - 1; i++)
        if (text.charAt(i) == " " && (best < 0 || Math.abs(i - text.length / 2) < Math.abs(best - text.length / 2))) best = i;
    return best;
}

// top: the top line, which is never split with a line break (see updateHeading) and keeps its leading.
// Returns how much it shrank the line (1 = not at all).
function fitArea(tf, start, len, label, before, name, top) {
    if (tf.kind != TextType.AREATEXT) return 1;
    unshrink(tf, start, len, label, top);
    var p = paraOf(tf, start), allowed = Math.max(1, before[p] || 1), k = 1;
    if (paraLines(tf, p) < 0) return 1;                     // Illustrator can't say; leave it
    function over() {                                       // a part pushed out, or more lines than allowed
        var parts = linesOfRange(tf, start, len), sum = 0;
        if (anyHidden(parts)) return true;
        for (var q = 0; q < parts.length; q++) sum += parts[q];
        return sum > allowed;
    }
    function shrinkTo(floor) { while (over() && k > floor) { scaleChars(tf, start, len, 0.95, top); k *= 0.95; } }
    shrinkTo(0.8);
    if (over() && allowed == 1 && !top) {
        // too long for one line: split it in two at the middle, as done by hand, back at full size
        var cut = balancedBreak(tf.contents.substr(start, len));
        if (cut > 0) {
            scaleChars(tf, start, len, 1 / k, top);
            k = 1;
            replaceAt(tf, start + cut, 1, "\u0003");
            allowed = 2;
            L("  split the " + name + " over two lines");
        }
    }
    shrinkTo(0.3);
    if (k < 1) {
        markShrink(tf, k, label);
        L("  shrank the " + name + " to " + Math.round(k * 100) + "% so it fits on " + (allowed == 1 ? "one line" : allowed + " lines"));
        if (k < 0.75) P("the " + name + " had to shrink to " + Math.round(k * 100) + "%; consider a shorter line or splitting it by hand");
    }
    if (over()) P("the " + name + " still doesn't fit; shorten or resize it by hand");
    return k;
}

// ---------- the rules beside the series line and the date ("—— Fall Series 4 ——") ----------

// Left/right edges and middle height of a text line: point text from its bounds; the first line
// of area text from an outlined copy (area text bounds are the box, not the glyphs).
function textExtent(tf, firstLineOnly) {
    if (!firstLineOnly) { var b = tf.geometricBounds; return { left: b[0], right: b[2], cy: (b[1] + b[3]) / 2 }; }
    var leaves = glyphBounds(tf);
    if (!leaves.length) return null;
    var top = leaves[0][1], bottom = leaves[0][3], left = leaves[0][0], right = leaves[0][2];
    for (var k = 1; k < leaves.length; k++) {
        var b2 = leaves[k];
        if ((leaves[k - 1][1] + leaves[k - 1][3]) / 2 - (b2[1] + b2[3]) / 2 > 15) break;   // next line
        top = Math.max(top, b2[1]); bottom = Math.min(bottom, b2[3]);
        left = Math.min(left, b2[0]); right = Math.max(right, b2[2]);
    }
    return { left: left, right: right, top: top, bottom: bottom, cy: (top + bottom) / 2 };
}

// Bounds of each glyph of a text frame, top line first, from an outlined copy that is then removed.
function glyphBounds(tf) {
    var dup = tf.duplicate(), g = null, leaves = [];
    try {
        g = dup.createOutline();
        (function walk(c) {
            for (var i = 0; i < c.pageItems.length; i++)
                if (c.pageItems[i].typename == "GroupItem") walk(c.pageItems[i]); else leaves.push(c.pageItems[i].geometricBounds);
        })(g);
    } catch (e) { L("  (couldn't measure '" + strip(tf.contents).substr(0, 20) + "': " + e + ")"); }
    try { (g || dup).remove(); } catch (e2) {}
    leaves.sort(function (x, y) { return (y[1] + y[3]) - (x[1] + x[3]); });
    return leaves;
}

// Thin horizontal paths level with a text line, entirely to its left (side -1) or right (+1).
function rulesBeside(doc, ext) {
    var out = [];
    for (var i = 0; i < doc.pathItems.length; i++) {
        var p = doc.pathItems[i];
        if (p.clipping || isHidden(p)) continue;
        var b = p.geometricBounds;
        if (b[1] - b[3] > 4 || b[2] - b[0] < 30 || Math.abs((b[1] + b[3]) / 2 - ext.cy) > 30) continue;
        if (b[2] <= ext.left + 2) out.push({ path: p, side: -1 });
        else if (b[0] >= ext.right - 2) out.push({ path: p, side: 1 });
    }
    return out;
}

var MIN_RULE = 60;                                        // points; shorter looks like a stray dash

// A (centered) line that grew so much that the rules beside it would drop below MIN_RULE gets
// shrunk instead, so the rules keep MIN_RULE and last week's gap. Returns the line's new extent.
// How much a line must shrink so the rules beside it keep MIN_RULE and last week's gap (1 = not at all).
function ruleRoom(rules, before, after) {
    var slack = 1e9;
    for (var i = 0; i < rules.length; i++) {
        var b = rules[i].path.geometricBounds;
        slack = Math.min(slack, (b[2] - b[0]) - MIN_RULE);      // how far this side's text may grow
    }
    var oldW = before.right - before.left, newW = after.right - after.left;
    if (rules.length == 0 || newW - oldW <= 2 * slack) return 1;
    return Math.max(0.3, (oldW + 2 * Math.max(0, slack)) / newW);
}

function fitBetweenRules(tf, start, len, label, name, rules, before, after) {
    var k = ruleRoom(rules, before, after), m = shrinkRe(label).exec(tf.note || "");
    if (k >= 1) return after;
    var total = k * (m ? parseFloat(m[1]) : 1);             // with any shrink to keep it on one line
    scaleChars(tf, start, len, k, true);
    markShrink(tf, total, label);
    L("  shrank the " + name + " to " + Math.round(k * 100) + "% so the rules beside it stay at least " + MIN_RULE + "pt");
    if (total < 0.75) P("the " + name + " had to shrink to " + Math.round(total * 100) + "% in all; consider a shorter line or splitting it by hand");
    return textExtent(tf, true) || after;
}

// Keep last week's gap between the text and each rule by moving the rule's inner end.
function moveRules(rules, before, after, label) {
    var moved = 0, cramped = false;
    for (var i = 0; i < rules.length; i++) {
        var r = rules[i], pts = r.path.pathPoints, b = r.path.geometricBounds;
        var d = r.side < 0 ? after.left - before.left : after.right - before.right;
        if (Math.abs(d) < 2) continue;                    // e.g. "Series 3" -> "Series 4": leave it be
        var innerX = r.side < 0 ? b[2] : b[0], outerX = r.side < 0 ? b[0] : b[2];
        var target = r.side < 0 ? Math.max(innerX + d, outerX + 20) : Math.min(innerX + d, outerX - 20);   // keep 20pt of rule
        if (target != innerX + d) cramped = true;
        unlock(r.path);
        for (var k = 0; k < pts.length; k++) {
            var a = pts[k].anchor;
            if (Math.abs(a[0] - innerX) > 1) continue;
            var dx = target - innerX, ld = pts[k].leftDirection, rd = pts[k].rightDirection;
            pts[k].anchor = [a[0] + dx, a[1]];
            pts[k].leftDirection = [ld[0] + dx, ld[1]];
            pts[k].rightDirection = [rd[0] + dx, rd[1]];
        }
        moved++;
    }
    if (moved) L("  moved " + moved + " rule" + (moved == 1 ? "" : "s") + " beside the " + label + " to keep the gap");
    if (cramped) P("the " + label + " is too wide for the rules beside it (down to 20pt, closer than last week's gap); check it");
}

// ---------- the three edits ----------

// The panel = the biggest visible filled rectangle on the right half. Clipping masks and white
// shapes are skipped: the template has a white clip path almost exactly the panel's size.
function findPanel(doc, ab) {
    var abW = ab[2] - ab[0], abH = ab[1] - ab[3], midX = ab[0] + abW / 2, panel = null, best = 0;
    for (var i = 0; i < doc.pathItems.length; i++) {
        var p = doc.pathItems[i];
        if (!p.filled || p.clipping || p.fillColor.typename != "RGBColor" || isHidden(p)) continue;
        var c = p.fillColor;
        if (c.red > 250 && c.green > 250 && c.blue > 250) continue;
        var b = p.geometricBounds, w = b[2] - b[0], h = b[1] - b[3];
        if (center(b)[0] > midX && h > abH * 0.9 && w * h > best) { best = w * h; panel = p; }
    }
    if (panel) L("  panel: " + rgbText(panel.fillColor) + " rectangle " + fmt(panel.geometricBounds));
    else P("panel rectangle not found");
    return panel;
}

function recolorPanel(doc, panel, hex) {
    if (!panel) return;
    var oldC = panel.fillColor, newC = hexColor(hex), n = 0;
    for (var j = 0; j < doc.pathItems.length; j++) {
        var q = doc.pathItems[j];
        if (q.filled && !q.clipping && sameRGB(q.fillColor, oldC)) { unlock(q); q.fillColor = newC; n++; }
    }
    L("  panel color: " + rgbText(oldC) + " -> " + hex + " (" + n + " shape" + (n == 1 ? "" : "s") + ")");
}

// The photo = the biggest visible image (linked or embedded) whose center is on the left half.
// It sits on top of everything, so it is clipped to its frame: a wide photo would otherwise
// cover the text panel.
function swapImage(doc, imgPath, ab, panel, D) {
    var midX = ab[0] + (ab[2] - ab[0]) / 2, target = null, best = 0, pools = [doc.placedItems, doc.rasterItems];
    for (var q = 0; q < pools.length; q++) for (var i = 0; i < pools[q].length; i++) {
        var it = pools[q][i], b = it.geometricBounds, a = (b[2] - b[0]) * (b[1] - b[3]), hid = isHidden(it);
        L("  image candidate: " + it.typename + " bounds " + fmt(b) + (hid ? " (hidden)" : ""));
        if (!hid && center(b)[0] < midX && a > best) { best = a; target = it; }
    }
    if (!target) { P("no photo found on the left side"); return; }
    unlock(target);

    // the frame to fill: last week's clipping mask if there is one, else the artboard left of the panel
    var par = target.parent, clipGroup = null, frame = null;
    if (par.typename == "GroupItem" && par.clipped) {
        clipGroup = par;
        for (var j = 0; j < par.pageItems.length; j++)
            if (par.pageItems[j].clipping) { frame = par.pageItems[j].geometricBounds; break; }
    }
    if (!frame) frame = [ab[0], ab[1], panel ? panel.geometricBounds[0] : midX, ab[3]];
    var fw = frame[2] - frame[0], fh = frame[1] - frame[3];

    var img = doc.placedItems.add();
    img.file = new File(imgPath);
    img.move(target, ElementPlacement.PLACEBEFORE);         // move() returns nothing; keep using img
    var nb = img.geometricBounds, s = Math.max(fw / (nb[2] - nb[0]), fh / (nb[1] - nb[3]));   // cover the frame
    img.resize(s * 100, s * 100, true, true, true, true, 100, Transformation.CENTER);

    // center it, then apply the week's nudge, kept small enough that the photo still fills the frame
    var sb = img.geometricBounds, c0 = center(frame), c1 = center(sb);
    var slackX = ((sb[2] - sb[0]) - fw) / 2, slackY = ((sb[1] - sb[3]) - fh) / 2;
    var wantX = num(D.photoShiftX), wantY = num(D.photoShiftY);
    var dx = clamp(wantX, -slackX, slackX), dy = clamp(wantY, -slackY, slackY);
    if (dx != wantX || dy != wantY)
        P("photo shift limited to " + Math.round(dx) + "," + Math.round(dy) + " so the photo still fills its frame");
    img.translate(c0[0] - c1[0] + dx, c0[1] - c1[1] + dy);

    var oldType = target.typename;
    target.remove();                        // don't read target after this
    if (!clipGroup) {
        var grp = img.parent.groupItems.add();
        grp.move(img, ElementPlacement.PLACEBEFORE);
        img.move(grp, ElementPlacement.PLACEATEND);
        grp.pathItems.rectangle(frame[1], frame[0], fw, fh);   // topmost path in the group becomes the mask
        grp.clipped = true;
    }
    L("  image: " + oldType + " -> " + imgPath.split("/").pop() + " at " + Math.round(s * 100) + "%, frame " + fmt(frame) +
      (dx || dy ? ", shifted " + Math.round(dx) + "," + Math.round(dy) : "") + (clipGroup ? " (kept last week's mask)" : " (clipped to frame)"));
    img.embed();                            // self-contained .ai: nothing to relink after the week moves to Past Weeks
}

function setTimeZone(tf, tz) {
    var s = tf.contents, re = /\b(PDT|PST)\b/gi, m, hits = [], n = 0;
    while ((m = re.exec(s)) != null) hits.push(m.index);
    for (var k = hits.length - 1; k >= 0; k--) {
        var old = s.substr(hits[k], 3), neu = old == old.toLowerCase() ? tz.toLowerCase() : tz.toUpperCase();
        if (old != neu) { replaceAt(tf, hits[k], 3, neu); n++; }
    }
    return n;
}

// ---------- the heading ("Fall Series 4" / "Painting the Stars" / "An Anticipatory Universe") ----------
// Page 1's lines above the date, one to four of them: an optional label ("Fall Series 4",
// "Pentecost 11", "Lent 4"), an optional series name, and the title. The first line has rules
// beside it. When the number of lines changes from last week's, lines are added above the title or
// removed below the first line (so each keeps a line's style, and the title keeps the title's), and
// the block is kept centered where last week's was.

// The heading = the right-panel text starting above the date with the most text in it (the header
// bar's "Sunday"/"Sermon" is left out; hidden and empty frames were skipped already). Its box is taller
// than its text, so after re-centering its middle can sit below the date; its top can't.
function findHeading(frames, dateTF, midX) {
    var best = null, most = 0, limit = dateTF ? dateTF.geometricBounds[1] : -1e9;
    for (var i = 0; i < frames.length; i++) {
        var tf = frames[i], b = tf.geometricBounds, t = strip(tf.contents);
        if (tf === dateTF || center(b)[0] < midX || b[1] <= limit || /^(Sunday|Sermon)$/i.test(t)) continue;
        if (t.length > most) { most = t.length; best = tf; }
    }
    return best;
}

// Undo every shrink recorded on the heading's lines ("line 2", or the older names) before its text
// changes. The top line was shrunk keeping its leading, so it's restored the same way.
function unshrinkAll(tf, lines) {
    var re = /\s*UpdateWeek shrink (line (\d+)|series line|series name|title) ([\d.]+)/g, note = tf.note || "", m;
    while ((m = re.exec(note)) != null) {
        var idx = 1;                                      // "series name" (no nested ?: - ExtendScript misparses it)
        if (m[2]) idx = parseInt(m[2], 10) - 1;
        else if (m[1] == "title") idx = lines.length - 1;
        else if (m[1] == "series line") idx = 0;
        if (lines[idx]) { var s = span(lines[idx]); scaleChars(tf, s.start, s.len, 1 / parseFloat(m[3]), idx == 0); }
    }
    tf.note = note.replace(/\s*UpdateWeek shrink (line \d+|series line|series name|title) [\d.]+/g, "")
                  .replace(/\s*UpdateWeek center [-\d.]+/g, "");      // from the version that moved the whole block
}

// Take back last week's centering (the leading added to the second line's first visual line), so the
// lines' own leading is what gets measured, remembered and reused.
function undoLift(tf, lines) {
    var m = /UpdateWeek lead (-?[\d.]+)/.exec(tf.note || "");
    if (!m) return;
    if (lines.length >= 2) {
        var fl = firstVisualLine(tf, lines[1]), d = parseFloat(m[1]);
        for (var i = fl.start; i < fl.start + fl.len; i++) {
            var a = tf.characters[i].characterAttributes;
            a.leading = a.leading - d;
        }
    }
    tf.note = tf.note.replace(/\s*UpdateWeek lead [-\d.]+/g, "");
}

function removeLine(tf, i) {
    var l = splitLines(tf.contents)[i], n = l.text.length + 1;      // the line and its paragraph break
    for (var k = 0; k < n; k++) tf.characters[l.start].remove();
}

// The type size and leading of the lines under the top one, remembered in the frame's note so a week
// with only a top line can still give next week's lines the right style.
function middleStyle(tf) {
    var m = /UpdateWeek middle ([\d.]+) ([\d.]+)/.exec(tf.note || "");
    return m ? { size: parseFloat(m[1]), leading: parseFloat(m[2]) } : null;
}
function rememberMiddleStyle(tf, lines) {
    if (lines.length < 2) return;
    var a = tf.characters[span(lines[1]).start].characterAttributes;
    tf.note = (tf.note || "").replace(/\s*UpdateWeek middle [\d.]+ [\d.]+/g, "") +
              " UpdateWeek middle " + a.size.toFixed(2) + " " + a.leading.toFixed(2);
}
function applyMiddleStyle(tf, start, len, style) {
    if (!style) return;
    for (var i = start; i < start + len; i++) {
        var a = tf.characters[i].characterAttributes;
        a.size = style.size;
        if (!a.autoLeading) a.leading = style.leading;
    }
}

// A placeholder line under the top one: above the title in the title's style, or, when the top line is
// all there is, right under it in the remembered middle style.
function insertMiddleLine(tf, style) {
    var ls = splitLines(tf.contents), c = ls.length;
    if (c >= 2) { replaceAt(tf, ls[c - 1].start, 1, "#\r" + tf.contents.charAt(ls[c - 1].start)); return; }
    var e = ls[0].start + ls[0].text.length;
    if (tf.contents.charAt(e) == "\r") replaceAt(tf, e, 1, "\r#\r");
    else replaceAt(tf, e - 1, 1, tf.contents.charAt(e - 1) + "\r#");
    applyMiddleStyle(tf, e + 1, 1, style);
}

// The heading lines: "heading = Fall Series 4 | Painting the Stars | An Anticipatory Universe", or the
// older series / seriesName / title fields.
function headingOf(d) {
    var parts = d.heading ? d.heading.split("|") : [d.series || "", d.seriesName || "", d.title || ""], out = [];
    for (var i = 0; i < parts.length; i++) if (strip(parts[i])) out.push(strip(parts[i]));
    return out;
}

// The top line's glyphs and those of everything under it ([top, bottom] each; rest is null when the top
// line is all there is), and the top of the date's glyphs.
function headingParts(tf) {
    var leaves = glyphBounds(tf), first = null, rest = null;
    for (var k = 0; k < leaves.length; k++) {
        var b = leaves[k], cy = (b[1] + b[3]) / 2;
        if (!first || (!rest && (first.cy - cy) <= 15)) {
            if (!first) first = { top: b[1], bottom: b[3], cy: cy };
            else { first.top = Math.max(first.top, b[1]); first.bottom = Math.min(first.bottom, b[3]); }
        } else if (!rest) rest = { top: b[1], bottom: b[3] };
        else { rest.top = Math.max(rest.top, b[1]); rest.bottom = Math.min(rest.bottom, b[3]); }
    }
    return { first: first, rest: rest };
}
function glyphTop(tf) {
    var leaves = glyphBounds(tf), top = -1e9;
    for (var k = 0; k < leaves.length; k++) top = Math.max(top, leaves[k][1]);
    return leaves.length ? top : tf.geometricBounds[1];
}

// The lines under the top one sit centered between it and the date. They're moved with the leading of
// their first line (the distance from the top line's baseline), which can pull them up as well as push
// them down; the top line's own leading, and so its place, isn't touched. If they don't fit with at
// least MIN_GAP above and below, they're shrunk evenly first (type and leading), a line at a time
// recorded so next week can undo it.
var MIN_GAP = 30;
function firstVisualLine(tf, line) {                   // chars of a line up to a forced line break
    var sp = span(line), cut = tf.contents.substr(sp.start, sp.len).indexOf("\u0003");
    return { start: sp.start, len: cut < 0 ? sp.len : cut };
}
function setLeading(tf, range, value) {
    for (var i = range.start; i < range.start + range.len; i++) {
        var a = tf.characters[i].characterAttributes;
        a.autoLeading = false;
        a.leading = Math.max(a.size * 0.5, value);
    }
}
function centerMiddle(tf, n, dateTF, roles) {
    var paras = tf.paragraphs.length, i;
    for (i = 1; i < paras; i++) { try { tf.paragraphs[i].paragraphAttributes.spaceBefore = 0; } catch (e) {} }
    if (n < 2 || !dateTF || tf.kind != TextType.AREATEXT) return;
    var dateTop = glyphTop(dateTF), total = 1, parts, gap = 0, free = 0;
    // Pull the lines up under the top one while they're fitted: at their usual spacing, the last of a
    // long heading can drop out of the bottom of the text box, which would look like it doesn't fit.
    var fl0 = firstVisualLine(tf, splitLines(tf.contents)[1]), a0 = tf.characters[fl0.start].characterAttributes, was0 = a0.leading;
    setLeading(tf, fl0, a0.size);
    function measure() {
        parts = headingParts(tf);
        if (!parts.first || !parts.rest) return false;
        free = (parts.first.bottom - dateTop) - (parts.rest.top - parts.rest.bottom);
        gap = free / 2;
        return true;
    }
    function middleCounts() {
        var ls = splitLines(tf.contents), a = span(ls[1]).start, z = span(ls[n - 1]);
        return linesOfRange(tf, a, z.start + z.len - a);
    }
    for (var tries = 0; tries < 14; tries++) {
        if (!measure()) break;
        var hidden = anyHidden(middleCounts());
        if ((!hidden && gap >= MIN_GAP) || total < 0.5) break;
        var cur = splitLines(tf.contents);
        for (i = 1; i < n && i < cur.length; i++) {
            var sp = span(cur[i]), m = shrinkRe("line " + (i + 1)).exec(tf.note || "");
            scaleChars(tf, sp.start, sp.len, 0.95);
            markShrink(tf, 0.95 * (m ? parseFloat(m[1]) : 1), "line " + (i + 1));
        }
        total *= 0.95;
    }
    if (total < 1) L("  shrank the lines under the top one to " + Math.round(total * 100) + "% so they fit above the date");
    if (total < 0.75) P("the lines under the top one had to shrink to " + Math.round(total * 100) + "%; check them");
    var fl = firstVisualLine(tf, splitLines(tf.contents)[1]);
    for (tries = 0; tries < 4 && parts && parts.rest; tries++) {
        var move = gap - (parts.first.bottom - parts.rest.top);   // + moves them down
        if (Math.abs(move) < 0.5) break;
        setLeading(tf, fl, tf.characters[fl.start].characterAttributes.leading + move);
        if (!measure()) break;
    }
    // How far the second line's leading is from its own (shrunk) leading, recorded so next week starts
    // from the lines' own leading (see undoLift).
    var lift = tf.characters[fl.start].characterAttributes.leading - was0 * total;
    tf.note = (tf.note || "").replace(/\s*UpdateWeek lead [-\d.]+/g, "") + " UpdateWeek lead " + lift.toFixed(3);
    if (!parts || !parts.rest) return;
    var above = parts.first.bottom - parts.rest.top, below = parts.rest.bottom - dateTop;
    L("  centered the " + (n == 2 ? roles[1] : "lines under the top one") + " between the top line and the date (" +
      Math.round(above) + "pt above, " + Math.round(below) + "pt below)");
    if (Math.abs(above - below) > 3) P("couldn't center the lines under the top one (" + Math.round(above) + "pt above, " +
                                        Math.round(below) + "pt below); check the spacing");
    if (anyHidden(middleCounts())) P("part of the heading is pushed out of its box; shorten the heading or fix it by hand");
}

function updateHeading(doc, tf, D, maxW, headerTF, dateTF) {
    var want = headingOf(D), i;
    var old = splitLines(tf.contents), m = old.length, oldText = [];
    if (!m) { P("the heading above the date is empty"); return; }
    for (i = 0; i < m; i++) oldText.push(strip(old[i].text));

    // measured as it looked last week
    var ext0 = textExtent(tf, true);
    var rules = ext0 ? rulesBeside(doc, ext0) : [];
    undoLift(tf, old);                                      // before unshrinking: it was applied after
    unshrinkAll(tf, old);
    rememberMiddleStyle(tf, old);
    var style = middleStyle(tf);

    // A lone line (a title and nothing else) too long to sit beside its rules is split into a top
    // line and one under it, as done by hand for "Fulfilling The Dream / For Freedom".
    if (want.length == 1 && rules.length && tf.kind == TextType.AREATEXT) {
        var probe = want[0], cut = balancedBreak(probe);
        if (cut > 0 && tooWideForTop(tf, old, probe, rules, ext0)) {
            want = [probe.substring(0, cut), probe.substring(cut + 1)];
            L("  split the title over two lines to leave room for the rules beside it");
        }
    }
    var n = want.length, roles = [];
    for (i = 0; i < n; i++) {                               // no nested ?: - ExtendScript misparses it
        if (i == n - 1) roles.push("title");
        else if (i == 0) roles.push("first heading line");
        else roles.push("heading line " + (i + 1));
    }

    // the same number of lines: the top line stays (its place is fixed), lines under it are removed,
    // or added above the title in the title's style
    for (i = 0; i < m - n; i++) removeLine(tf, 1);
    for (i = m; i < n; i++) insertMiddleLine(tf, style);
    var cur = splitLines(tf.contents);
    for (i = n - 1; i >= 0; i--) {                              // from the end so earlier offsets stay put
        var s = span(cur[i]);
        if (s.len == 0) replaceAt(tf, cur[i].start, 1, want[i] + tf.contents.charAt(cur[i].start));
        else if (s.text != want[i]) replaceAt(tf, s.start, s.len, want[i]);
    }
    for (i = 0; i < n; i++)
        if (n != m || oldText[i] != want[i]) L("  " + roles[i] + ": " + (n == m ? oldText[i] + " -> " : "") + want[i]);
    if (n != m) L("  heading: " + m + " line" + (m == 1 ? "" : "s") + " -> " + n + " (was " + oldText.join(" / ") + ")");

    // fit every line again (all were put back to full size), top to bottom
    if (tf.kind != TextType.AREATEXT) { fitWidth(tf, maxW); return; }
    cur = splitLines(tf.contents);
    for (i = 0; i < n; i++) { var s1 = span(cur[i]); fitArea(tf, s1.start, s1.len, "line " + (i + 1), [], roles[i], i == 0); }
    if (rules.length && ext0) {
        var s0 = span(splitLines(tf.contents)[0]), ext2 = textExtent(tf, true);
        if (ext2) moveRules(rules, ext0, fitBetweenRules(tf, s0.start, s0.len, "line 1", roles[0], rules, ext0, ext2), roles[0]);
    }
    centerMiddle(tf, n, dateTF, roles);
}

// Would this text, as the top line, shrink below 80% to fit the panel and keep its rules at MIN_RULE?
// Tried on a copy of the frame, which is then removed.
function tooWideForTop(tf, old, text, rules, ext0) {
    var dup = tf.duplicate(), result = false;
    try {
        var l0 = span(splitLines(dup.contents)[0]);
        replaceAt(dup, l0.start, l0.len, text);
        var k = fitArea(dup, l0.start, text.length, "probe", [1], "title", true), ext = textExtent(dup, true);
        result = !ext || k * ruleRoom(rules, ext0, ext) < 0.8;
    } catch (e) {}
    try { dup.remove(); } catch (e2) {}
    return result;
}

function updateText(doc, job, D, ab) {
    var abW = ab[2] - ab[0], abH = ab[1] - ab[3], midX = ab[0] + abW / 2, maxW = abW * 0.47;
    var dateRe = new RegExp("^(" + MONTHS + ")\\s+\\d{1,2},\\s*\\d{4}$", "i");
    var titledRe = /^(The\s+)?(Rev\.|Reverend|Dr\.|Pastor|Rabbi|Minister|Elder|Chaplain)\s/i;
    var dateTF = null, preacherTF = null, headerTF = null, frames = [], others = [], tzFrames = [];
    for (var i = 0; i < doc.textFrames.length; i++) {
        var tf = doc.textFrames[i];
        if (isHidden(tf)) continue;                      // e.g. the hidden JUNETEENTH frame
        var t = strip(tf.contents);
        if (t == "") continue;
        frames.push(tf);
        if (/\b(PDT|PST)\b/i.test(t)) tzFrames.push(tf);
        if (!dateTF && dateRe.test(t)) dateTF = tf;
        else if (!preacherTF && titledRe.test(t) && t.indexOf("\r") < 0) preacherTF = tf;
        else if (!headerTF && /^(Sunday|Sermon)$/i.test(t)) headerTF = tf;
        else others.push(tf);
    }

    if (dateTF) {
        var od = strip(dateTF.contents), dBefore = textExtent(dateTF), dRules = rulesBeside(doc, dBefore);
        replaceAt(dateTF, dateTF.contents.indexOf(od), od.length, D.dateText); fitWidth(dateTF, maxW);
        L("  date: " + od + " -> " + D.dateText);
        moveRules(dRules, dBefore, textExtent(dateTF), "date");
    } else P("date line not found");

    if (job.kind == "sermon") {
        if (!preacherTF && dateTF) {
            // a name without Rev./Dr.: the one-line frame just below the date, above the church name
            var top = dateTF.geometricBounds[3], bestGap = 1e9;
            for (var p = 0; p < others.length; p++) {
                var ob = others[p].geometricBounds, oc = center(ob), ot = strip(others[p].contents);
                if (oc[0] > midX && oc[1] < top && oc[1] > ab[3] + abH * 0.15 && ot.indexOf("\r") < 0 && top - ob[1] < bestGap) {
                    bestGap = top - ob[1]; preacherTF = others[p];
                }
            }
        }
        if (preacherTF) {
            var op = strip(preacherTF.contents);
            replaceAt(preacherTF, preacherTF.contents.indexOf(op), op.length, D.preacher); fitWidth(preacherTF, maxW);
            L("  preacher: " + op + " -> " + D.preacher);
        } else P("preacher line not found");
    }

    if (D.timeZone) for (var z = 0; z < tzFrames.length; z++) {
        var nz = setTimeZone(tzFrames[z], D.timeZone);
        if (nz) L("  time zone: " + nz + " time" + (nz == 1 ? "" : "s") + " -> " + D.timeZone);
    }

    var heading = findHeading(frames, dateTF, midX);
    if (heading) updateHeading(doc, heading, D, maxW, headerTF, dateTF);
    else P("the heading above the date wasn't found");
}

// ---------- main ----------

function runJob(job, D, image) {
    L("");
    L(job.name);
    L("  template: " + job.template);
    var dir = new Folder(job.dir); if (!dir.exists) dir.create();
    var doc = app.open(new File(job.template));
    var restore = unlockAll(doc);
    var ab = doc.artboards[0].artboardRect; // [left, top, right, bottom]
    relock = [];
    try {
        var panel = findPanel(doc, ab);
        swapImage(doc, image, ab, panel, D);
        updateText(doc, job, D, ab);
        recolorPanel(doc, panel, D.panelColor);
    } catch (e) { P("error: " + e + " (line " + e.line + ")"); }
    for (var r = 0; r < relock.length; r++) try { relock[r].locked = true; } catch (e2) {}
    restore();
    var so = new IllustratorSaveOptions(); so.pdfCompatible = true;
    doc.saveAs(new File(job.dir + "/" + job.name + ".ai"), so);
    var jo = new ExportOptionsJPEG();
    jo.artBoardClipping = true; jo.antiAliasing = true; jo.qualitySetting = 80;
    jo.horizontalScale = 100; jo.verticalScale = 100;
    var want = new File(job.dir + "/" + job.name + ".jpg"); if (want.exists) want.remove();
    doc.exportFile(want, ExportType.JPEG, jo);
    var got = new File(job.dir + "/" + job.name.replace(/ /g, "-") + ".jpg");   // Illustrator swaps spaces for hyphens
    if (!want.exists && got.exists) got.rename(job.name + ".jpg");
    doc.close(SaveOptions.DONOTSAVECHANGES);
    L("  saved " + job.name + ".ai + .jpg");
}

function UpdateWeek() {
    // Tests call this with $.global.UPDATEWEEK_DATA / UPDATEWEEK_QUIET set. Illustrator keeps globals
    // between runs, so clear them at once: a later File > Scripts run must not pick them up.
    var override = $.global.UPDATEWEEK_DATA, quiet = $.global.UPDATEWEEK_QUIET === true;
    $.global.UPDATEWEEK_DATA = undefined; $.global.UPDATEWEEK_QUIET = undefined;
    function tell(msg) { if (!quiet) alert(msg); }

    var dataFile = override ? new File(override) : new File(File($.fileName).parent.fsName + "/week-data.txt");
    if (!dataFile.exists) {
        if (quiet) return "!! no week-data.txt at " + dataFile.fsName;
        dataFile = File.openDialog("Choose this week's week-data.txt");
        if (!dataFile) return "cancelled";
    }
    var D = readData(dataFile), bad = validate(D);
    if (bad.length) { tell("week-data.txt has problems, nothing was changed:\n\n" + bad.join("\n")); return "!! " + bad.join("\n!! "); }

    var root = resolvePath(D.root, "");
    var out = resolvePath(D.outputFolder, root), image = resolvePath(D.imagePath, root);
    var jobs = [
        { kind: "service", template: resolvePath(D.serviceTemplate, root), dir: out + "/" + D.serviceFolder, name: D.serviceName },
        { kind: "sermon", template: resolvePath(D.sermonTemplate, root), dir: out + "/" + D.sermonFolder, name: D.sermonName }
    ];

    // check everything before touching anything (and before alerts are switched off)
    bad = [];
    if (!new File(image).exists) bad.push("photo not found: " + image);
    for (var i = 0; i < jobs.length; i++) {
        var j = jobs[i], found = findTemplate(j.template, root);
        if (!found) { bad.push("template not found: " + j.template); continue; }
        j.moved = found != j.template; j.template = found;
        if (isOpen(found)) bad.push("close " + found.split("/").pop() + " in Illustrator first");
        if (isOpen(j.dir + "/" + j.name + ".ai")) bad.push("close " + j.name + ".ai in Illustrator first");
    }
    if (bad.length) { tell("Nothing was changed:\n\n" + bad.join("\n")); return "!! " + bad.join("\n!! "); }
    var todo = [];
    for (var k = 0; k < jobs.length; k++) {
        if (!quiet && new File(jobs[k].dir + "/" + jobs[k].name + ".ai").exists &&
            !confirm(jobs[k].name + ".ai already exists.\n\nReplace it and its .jpg?")) continue;
        todo.push(jobs[k]);
    }
    if (!todo.length) return "nothing to do";

    var summary = headingOf(D).concat([D.dateText, D.preacher, D.panelColor, D.timeZone]), shown = [];
    for (var sI = 0; sI < summary.length; sI++) if (summary[sI]) shown.push(summary[sI]);
    L("UpdateWeek: " + shown.join(" / "));
    L("data: " + dataFile.fsName);
    L("photo: " + image);
    var outDir = new Folder(out); if (!outDir.exists) outDir.create();
    var prevUI = app.userInteractionLevel;
    app.userInteractionLevel = UserInteractionLevel.DONTDISPLAYALERTS;   // the template has a missing link
    try {
        for (var t = 0; t < todo.length; t++) {
            if (todo[t].moved) L("(template was not where week-data.txt said; using the one found)");
            runJob(todo[t], D, image);
        }
    } catch (e) { P("fatal: " + e + " (line " + e.line + ")"); }
    app.userInteractionLevel = prevUI;

    L("");
    L(problems.length ? problems.length + " problem(s); search for !! above" : "done, no problems");
    var lf = new File(out + "/log.txt"); lf.encoding = "UTF-8"; lf.lineFeed = "Unix";
    lf.open("w"); lf.write(log.join("\n") + "\n"); lf.close();
    var keep = new File(out + "/week-data.txt");                         // a record of what built this week
    if (keep.fsName != dataFile.fsName) { if (keep.exists) keep.remove(); dataFile.copy(keep.fsName); }

    tell(problems.length
        ? "Finished with " + problems.length + " problem(s):\n\n" + problems.slice(0, 6).join("\n") + "\n\nSee log.txt in " + outDir.name
        : "Done. " + todo.length + " graphic(s) saved in " + decodeURI(outDir.name) + ".\nCheck the JPGs and log.txt.");
    return log.join("\n");
}

UpdateWeek();
