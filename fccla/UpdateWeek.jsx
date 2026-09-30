// UpdateWeek.jsx - builds this week's Service Title + Sermon Title graphics from last week's .ai files.
// Run in Illustrator: File > Scripts > Other Script...
//
// Everything that changes week to week lives in week-data.txt (next to this script), written by
// prepare_week.py / Gemini from the Order of Worship PDF. This file should not need edits week to
// week; see README.md and GEMINI.md.
#target illustrator

var MONTHS = "January|February|March|April|May|June|July|August|September|October|November|December";
var REQUIRED = ["root", "series", "seriesName", "title", "dateText", "preacher", "panelColor", "imagePath",
    "outputFolder", "serviceTemplate", "serviceFolder", "serviceName", "sermonTemplate", "sermonFolder", "sermonName"];
var OPTIONAL = ["timeZone", "photoShiftX", "photoShiftY"];

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
    for (var i = 0; i < REQUIRED.length; i++) if (!d[REQUIRED[i]]) bad.push("missing " + REQUIRED[i]);
    for (k in d) if (!has(REQUIRED, k) && !has(OPTIONAL, k)) bad.push("unknown key " + k + " (typo?)");
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

// Lines of a frame with their character offsets (paragraphs end in \r; \u0003 is a forced line break).
function splitLines(s) {
    var out = [], start = 0;
    for (var i = 0; i <= s.length; i++) {
        var ch = i < s.length ? s.charAt(i) : "\r";
        if (ch == "\r" || ch == "\n" || ch == "\u0003") { out.push({ start: start, text: s.substring(start, i) }); start = i + 1; }
    }
    while (out.length && strip(out[out.length - 1].text) == "") out.pop();   // the title frame ends in an empty line
    return out;
}
function span(line) {
    var t = strip(line.text);
    return { start: line.start + line.text.indexOf(t), len: t.length, text: t };
}

function scaleChars(tf, start, len, k) {
    for (var i = start; i < start + len; i++) {
        var a = tf.characters[i].characterAttributes;
        a.size = a.size * k;
    }
}

// New text inherits the size of the text it replaces, so a shrink made to fit a long line would
// carry into next week's short one. Each shrink is recorded in the frame's note (Attributes panel),
// per line ("UpdateWeek shrink title 0.8000"), and undone before the next week's text is fitted.
function shrinkRe(label) { return new RegExp("\\s*UpdateWeek shrink " + label + " ([\\d.]+)"); }
function unshrink(tf, start, len, label) {
    var re = shrinkRe(label), m = re.exec(tf.note || "");
    if (!m) return;
    scaleChars(tf, start, len, 1 / parseFloat(m[1]));
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
function paraOf(tf, offset) {
    var c = tf.contents, n = 0;
    for (var i = 0; i < offset; i++) if (c.charAt(i) == "\r") n++;
    return n;
}
function paraLines(tf, p) { try { return tf.paragraphs[p].lines.length; } catch (e) { return -1; } }   // 0 = pushed out
function linesPerParagraph(tf) {
    var n = [];
    for (var p = 0; p < tf.paragraphs.length; p++) n.push(paraLines(tf, p));
    return n;
}
function fitArea(tf, start, len, label, before) {
    if (tf.kind != TextType.AREATEXT) return;
    unshrink(tf, start, len, label);
    var p = paraOf(tf, start), allowed = Math.max(1, before[p] || 1), k = 1;
    if (paraLines(tf, p) < 0) return;                       // Illustrator can't say; leave it
    function over() { var n = paraLines(tf, p); return n == 0 || n > allowed; }
    while (over() && k > 0.3) { scaleChars(tf, start, len, 0.95); k *= 0.95; }
    if (k < 1) {
        markShrink(tf, k, label);
        L("  shrank the " + label + " to " + Math.round(k * 100) + "% so it fits on " + (allowed == 1 ? "one line" : allowed + " lines"));
        if (k < 0.75) P("the " + label + " had to shrink to " + Math.round(k * 100) + "%; consider a shorter line or splitting it by hand");
    }
    if (over()) P("the " + label + " still doesn't fit; shorten or resize it by hand");
}

// ---------- the rules beside the series line and the date ("—— Fall Series 4 ——") ----------

// Left/right edges and middle height of a text line: point text from its bounds; the first line
// of area text from an outlined copy (area text bounds are the box, not the glyphs).
function textExtent(tf, firstLineOnly) {
    if (!firstLineOnly) { var b = tf.geometricBounds; return { left: b[0], right: b[2], cy: (b[1] + b[3]) / 2 }; }
    var dup = tf.duplicate(), g = null, leaves = [], ext = null;
    try {
        g = dup.createOutline();
        (function walk(c) {
            for (var i = 0; i < c.pageItems.length; i++)
                if (c.pageItems[i].typename == "GroupItem") walk(c.pageItems[i]); else leaves.push(c.pageItems[i].geometricBounds);
        })(g);
        leaves.sort(function (x, y) { return (y[1] + y[3]) - (x[1] + x[3]); });   // top line first
        var top = leaves[0][1], bottom = leaves[0][3], left = leaves[0][0], right = leaves[0][2];
        for (var k = 1; k < leaves.length; k++) {
            var b2 = leaves[k];
            if ((leaves[k - 1][1] + leaves[k - 1][3]) / 2 - (b2[1] + b2[3]) / 2 > 15) break;   // next line
            top = Math.max(top, b2[1]); bottom = Math.min(bottom, b2[3]);
            left = Math.min(left, b2[0]); right = Math.max(right, b2[2]);
        }
        ext = { left: left, right: right, cy: (top + bottom) / 2 };
    } catch (e) { L("  (couldn't measure '" + strip(tf.contents).substr(0, 20) + "': " + e + ")"); }
    try { (g || dup).remove(); } catch (e2) {}
    return ext;
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
function fitBetweenRules(tf, start, len, label, rules, before, after) {
    var slack = 1e9;
    for (var i = 0; i < rules.length; i++) {
        var b = rules[i].path.geometricBounds;
        slack = Math.min(slack, (b[2] - b[0]) - MIN_RULE);      // how far this side's text may grow
    }
    var oldW = before.right - before.left, newW = after.right - after.left;
    if (rules.length == 0 || newW - oldW <= 2 * slack) return after;
    var k = Math.max(0.3, (oldW + 2 * Math.max(0, slack)) / newW), m = shrinkRe(label).exec(tf.note || "");
    scaleChars(tf, start, len, k);
    markShrink(tf, k * (m ? parseFloat(m[1]) : 1), label);
    L("  shrank the " + label + " to " + Math.round(k * 100) + "% so the rules beside it stay at least " + MIN_RULE + "pt");
    if (k < 0.75) P("the " + label + " had to shrink to " + Math.round(k * 100) + "% to leave room for the rules beside it; check it");
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

function updateText(doc, job, D, ab) {
    var abW = ab[2] - ab[0], abH = ab[1] - ab[3], midX = ab[0] + abW / 2, maxW = abW * 0.47;
    var dateRe = new RegExp("^(" + MONTHS + ")\\s+\\d{1,2},\\s*\\d{4}$", "i");
    var seriesRe = /([A-Za-z]+[\s ]+Series[\s ]*\d+)/i;
    var titledRe = /^(The\s+)?(Rev\.|Reverend|Dr\.|Pastor|Rabbi|Minister|Elder|Chaplain)\s/i;
    var seriesTF = null, dateTF = null, preacherTF = null, others = [], tzFrames = [];
    for (var i = 0; i < doc.textFrames.length; i++) {
        var tf = doc.textFrames[i];
        if (isHidden(tf)) continue;                      // e.g. the hidden JUNETEENTH frame
        var t = strip(tf.contents);
        if (t == "") continue;
        if (/\b(PDT|PST)\b/i.test(t)) tzFrames.push(tf);
        if (!seriesTF && seriesRe.test(t)) seriesTF = tf;
        else if (!dateTF && dateRe.test(t)) dateTF = tf;
        else if (!preacherTF && titledRe.test(t) && t.indexOf("\r") < 0) preacherTF = tf;
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

    if (!seriesTF) { P("series line not found"); return; }
    var lines = splitLines(seriesTF.contents), si = 0;
    while (si < lines.length && !seriesRe.test(lines[si].text)) si++;
    var oldSeries = lines[si].text.match(seriesRe)[1], seriesAt = lines[si].start + lines[si].text.indexOf(oldSeries);

    // the rules beside the series line only need to move when the series text changes width
    var sBefore = null, sRules = [];
    if (oldSeries != D.series && si == 0) {
        sBefore = textExtent(seriesTF, true);
        if (sBefore) sRules = rulesBeside(doc, sBefore);
    }

    if (lines.length - si >= 3) {
        // series / series name / title all in one frame. Edit from the end so earlier offsets stay put.
        // A title that was split over several lines is replaced as a whole.
        var before = linesPerParagraph(seriesTF);
        var t0 = span(lines[si + 2]), t1 = span(lines[lines.length - 1]), nm = span(lines[si + 1]);
        var oTitle = seriesTF.contents.substring(t0.start, t1.start + t1.len);
        replaceAt(seriesTF, t0.start, t1.start + t1.len - t0.start, D.title);
        L("  title: " + oTitle.replace(/[\r\n\u0003]/g, " / ") + " -> " + D.title);
        var nameChanged = nm.text != D.seriesName;
        if (nameChanged) { replaceAt(seriesTF, nm.start, nm.len, D.seriesName); L("  series name: " + nm.text + " -> " + D.seriesName); }
        replaceAt(seriesTF, seriesAt, oldSeries.length, D.series); L("  series: " + oldSeries + " -> " + D.series);
        // fit top to bottom, since a line that wraps pushes the ones below it out of the box
        var ns = splitLines(seriesTF.contents), s0 = span(ns[si]), n1 = span(ns[si + 1]), last = span(ns[ns.length - 1]);
        if (oldSeries != D.series) fitArea(seriesTF, s0.start, s0.len, "series line", before);
        if (nameChanged) fitArea(seriesTF, n1.start, n1.len, "series name", before);
        fitArea(seriesTF, last.start, last.len, "title", before);
        if (sRules.length) {
            var sAfter = textExtent(seriesTF, true);
            if (sAfter) moveRules(sRules, sBefore, fitBetweenRules(seriesTF, s0.start, s0.len, "series line", sRules, sBefore, sAfter), "series line");
        }
        return;
    }
    replaceAt(seriesTF, seriesAt, oldSeries.length, D.series); L("  series: " + oldSeries + " -> " + D.series);
    fitWidth(seriesTF, maxW);
    if (sRules.length) moveRules(sRules, sBefore, textExtent(seriesTF, true), "series line");
    // otherwise: the title text sits in its own frame between the series line and the date line
    if (!dateTF) return;
    var top2 = seriesTF.geometricBounds[3], bottom = dateTF.geometricBounds[1], block = [];
    for (var k = 0; k < others.length; k++) {
        var cy = center(others[k].geometricBounds)[1];
        if (cy < top2 && cy > bottom) block.push(others[k]);
    }
    block.sort(function (x, y) { return y.geometricBounds[1] - x.geometricBounds[1]; });
    if (!block.length) { P("title text not found"); return; }
    var lf = block[block.length - 1], ll = splitLines(lf.contents), ot = span(ll[ll.length - 1]);
    replaceAt(lf, ot.start, ot.len, D.title); fitWidth(lf, maxW); L("  title: " + ot.text + " -> " + D.title);
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

    L("UpdateWeek: " + D.series + " / " + D.seriesName + " / " + D.title + " / " + D.dateText + " / " +
      D.preacher + " / " + D.panelColor + (D.timeZone ? " / " + D.timeZone : ""));
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
