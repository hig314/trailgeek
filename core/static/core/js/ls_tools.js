/* Copied verbatim from landslidescience inventory/static/inventory/js/ls_tools.js
 * (hig314/landslidescience @ e152641, 2026-10-18). One copy of every shared
 * thing: do not edit here; change it there and run
 * `python tools/sync_shared.py sync` (tools/shared.json pins the commit). */
/* ls_tools.js — pointer policy for a MapLibre map: who owns a click, who owns
 * a drag, who owns the cursor, what Escape means. Global `window.LSTools`.
 *
 * Loaded by /inventory/ (map.js) and, since 2026-10-11, by /lidar/ for its
 * landform trace tool (init with {position: 'top-right'}). Built as a module
 * rather than a pattern for exactly that second page.
 *
 * WHAT THIS REPLACES, AND WHY (2026-09-24)
 * ----------------------------------------
 * The click tools were already ONE control with radio semantics (the tool
 * group below, unchanged). What they did not own was everything around a
 * click. A survey found:
 *   - the guard `__measureActive || __drawActive || __insarActive` typed
 *     verbatim at 22 sites, a different guard in scarps.js, and none at all on
 *     two hover handlers;
 *   - `__reviseActive` written twice and read once, so a landslide click
 *     mid-outline-edit opened a detail panel on top of the editor;
 *   - three hand-rolled pointer-capture drags with no pointercancel, so an
 *     interrupted gesture left `dragging` stuck true;
 *   - a tool's crosshair and a layer's hover pointer writing the same
 *     property with no precedence, so hovering a scarp mid-measure replaced
 *     the crosshair;
 *   - Escape meaning three different things in three tools;
 *   - and the case that prompted the review: an external-inventory point
 *     inside one of our polygons opened its popup AND our panel by two paths
 *     that knew nothing of each other, while the same point on one of our
 *     dots opened only the panel — the difference decided by which of our
 *     layers happened to be underneath, by omission.
 * Hig: the co-activation is fine, possibly ideal, but "semantically vague".
 * So the policy is now declared in one place and every handler asks it.
 *
 * THE TOOL GROUP (history, from map.js 2026-09-10)
 * -----------------------------------------------
 * Measure (distance / area), Draw and InSAR all claim map clicks, so at most
 * one can be live at a time. They used to be three separate control groups
 * policing each other by hand, and doing it inconsistently: Draw alerted
 * "Exit the measure tool first"; InSAR alerted "Exit the measure/draw tool
 * first"; Measure SILENTLY refused to start while Draw was on — a dead button
 * with no explanation; Measure never checked InSAR at all, so measuring while
 * InSAR was live left two tools fighting over the same click. Hig: they are
 * "similar in type but mutually exclusive since they both respond to
 * click-on-map" — so one control, exclusivity structural rather than policed.
 * Picking one RELEASES whichever was live instead of refusing; picking the
 * live one again returns to plain inspect mode. An outgoing tool can object
 * via `canRelease` (Draw does, when a ring is still open), the one case where
 * a switch should ask rather than just happen. `order` fixes the button
 * sequence independently of registration order, because the tools register
 * whenever their own setup runs and two of them are conditional.
 *
 * THE POLICY, STATED ONCE
 * -----------------------
 * A "holder" is a tool that owns the map's clicks: a mode button in the
 * group, or a headless holder (the reviser) registered with hold(). At most
 * one holder is active. `blocked()` is the single question every non-tool
 * handler asks.
 *
 * Clicks: one map.on('click') here. Every clickable thing registers with a
 * KIND, and the kind decides what happens when several are under the cursor:
 *   action     runs only while its holder is active (measure vertex, InSAR
 *              sample). Nothing else runs while a holder is active.
 *   navigate   leaves the page or opens a modal (pending → review form,
 *              photo → lightbox). Highest one wins, nothing else runs.
 *   record     opens THE detail panel for one of our records. Highest wins.
 *   reference  opens a popup about somebody else's data or a companion layer
 *              (faults, scarps, staged polygons, external mirrors). Highest
 *              wins, and it may run ALONGSIDE a record — that is the declared
 *              co-activation. There is one popup slot, so a new reference
 *              popup closes the previous one; two from one click would sit on
 *              top of each other with the lower unreadable. A reference with
 *              no `layers` and a `test(e)` predicate is a RASTER reference
 *              (bedrock geology): it has no features to hit, so it is in the
 *              contest whenever its test says the raster is under the click,
 *              and its handler gets (null, e, []).
 *   probe      runs ALONE on an Alt-click (Option-click on a Mac) when no
 *              holder is active: a read-the-ground-here shortcut (the
 *              elevation probe) that must not also open whatever record or
 *              reference sits under the cursor. Ctrl-click is taken -- it is
 *              the Mac's right-click, which copies coordinates -- and
 *              Shift-drag is box zoom. While a holder is active an Alt-click
 *              is an ordinary click for that holder.
 *
 * Cursor: two owners, `tool` and `hover`; tool outranks hover. A layer's
 * mouseenter/mouseleave can set and clear its pointer unconditionally,
 * because it can never overwrite an active tool's crosshair.
 *
 * Drag: drag(el, …) does setPointerCapture and ends a gesture EXACTLY once,
 * on pointerup OR pointercancel OR lostpointercapture, then releases capture.
 * lostpointercapture also fires after every ordinary pointerup (the implicit
 * release), which is why ending is keyed on the pointerId and idempotent.
 *
 * Escape: one document keydown, routed to the active holder's cancel().
 * Meaning is two-step (Hig, 2026-09-24): cancel what is in progress; with
 * nothing in progress, exit the tool. Draw is the exception — Terra Draw owns
 * Escape there and cancels the ring itself; a second listener racing it on
 * the same keypress is exactly the kind of bug this file exists to remove.
 */
window.LSTools = (function () {
    'use strict';

    var map = null;
    var container = null, added = false;
    var position = 'top-left';  // where the tool group sits; init's opts.position
    var reg = {};              // id -> {onRelease, canRelease, cancel, btn}
    var activeId = null;

    // ---- the tool group -------------------------------------------------
    function ensure() {
        if (container) return container;
        container = document.createElement('div');
        container.className = 'maplibregl-ctrl maplibregl-ctrl-group inv-tools-ctrl';
        container.setAttribute('role', 'radiogroup');
        container.setAttribute('aria-label', 'Map click tools');
        return container;
    }
    function attach() {
        if (added) return;
        // A silent no-op here would leave the tool group off the map: the
        // measure and draw controls register their buttons from their own
        // onAdd, after init() has run, and would never notice.
        if (!map) throw new Error('LSTools.init(map) must run before a tool registers');
        added = true;
        var el = ensure();
        map.addControl({
            onAdd: function () { return el; },
            onRemove: function () {}
        }, position);
    }
    // Insert by `order` so the group always reads line, area, draw, InSAR,
    // clear — whatever sequence the tools happened to register in.
    function place(btn, order) {
        var el = ensure();
        btn.dataset.order = order;
        var kids = el.children, before = null;
        for (var i = 0; i < kids.length; i++) {
            if ((+kids[i].dataset.order || 0) > order) { before = kids[i]; break; }
        }
        el.insertBefore(btn, before);
        attach();
    }
    function mkBtn(o) {
        var b = document.createElement('button');
        b.type = 'button';
        b.title = o.title;
        b.setAttribute('aria-label', o.aria || o.title);
        b.textContent = o.label;
        if (o.className) b.className = o.className;
        return b;
    }
    function setBtn(id, on) {
        var r = reg[id];
        if (!r || !r.btn) return;
        r.btn.classList.toggle('active', !!on);
        r.btn.setAttribute('aria-checked', on ? 'true' : 'false');
    }

    // ---- cursor: tool outranks hover ------------------------------------
    var cur = { tool: null, hover: null };
    function applyCursor() {
        if (!map) return;
        map.getCanvas().style.cursor = cur.tool || cur.hover || '';
    }
    var cursor = {
        set: function (owner, value) { cur[owner] = value || null; applyCursor(); },
        clear: function (owner) { cur[owner] = null; applyCursor(); }
    };

    // ---- the one reference-popup slot -----------------------------------
    var slot = null;
    var popup = {
        // Show a MapLibre Popup in the slot, closing whatever was there.
        // Returns it. A popup the user closes clears the slot itself.
        show: function (p) {
            if (slot && slot !== p) { try { slot.remove(); } catch (e) {} }
            slot = p;
            if (p && typeof p.on === 'function') {
                p.on('close', function () { if (slot === p) slot = null; });
            }
            return p;
        },
        close: function () {
            if (!slot) return;
            var p = slot; slot = null;
            try { p.remove(); } catch (e) {}
        }
    };

    // ---- click dispatcher -----------------------------------------------
    var entries = [];
    function layersOf(en) {
        var L = typeof en.layers === 'function' ? en.layers() : en.layers;
        return L || [];
    }
    function dispatch(e) {
        var oe = e.originalEvent;
        if (!activeId && oe && oe.altKey) {
            var pr = entries.filter(function (en) { return en.kind === 'probe'; });
            if (pr.length) { pr[pr.length - 1].handler(null, e, []); return; }
        }
        if (activeId) {
            // A holder owns the map: only its own action entries run.
            entries.forEach(function (en) {
                if (en.kind === 'action' && en.holders &&
                    en.holders.indexOf(activeId) >= 0) en.handler(null, e, []);
            });
            return;
        }
        // One query over every registered layer that is actually in the
        // style. A layer id the style does not have makes MapLibre log an
        // error and return nothing, so the intersection is not optional.
        var wanted = [], owner = {};
        entries.forEach(function (en) {
            if (en.kind === 'action') return;
            layersOf(en).forEach(function (id) {
                if (!owner[id] && map.getLayer(id)) { owner[id] = en; wanted.push(id); }
            });
        });
        var hits = wanted.length ? (map.queryRenderedFeatures(e.point, { layers: wanted }) || []) : [];
        // Group by entry. Query order is render order, topmost first, so the
        // first feature an entry sees is its topmost — that is the one its
        // handler gets.
        var byEntry = [];
        entries.forEach(function (en) {
            if (en.kind === 'reference' && typeof en.test === 'function' &&
                !layersOf(en).length && en.test(e)) byEntry.push({ en: en, hits: [] });
        });
        hits.forEach(function (f) {
            var en = owner[f.layer && f.layer.id];
            if (!en) return;
            var g = null;
            for (var i = 0; i < byEntry.length; i++) if (byEntry[i].en === en) { g = byEntry[i]; break; }
            if (!g) { g = { en: en, hits: [] }; byEntry.push(g); }
            g.hits.push(f);
        });
        function best(kind) {
            var b = null;
            byEntry.forEach(function (g) {
                if (g.en.kind !== kind) return;
                if (!b || (g.en.priority || 0) > (b.en.priority || 0)) b = g;
            });
            return b;
        }
        if (!byEntry.length) return;
        var nav = best('navigate');
        if (nav) { nav.en.handler(nav.hits[0], e, nav.hits); return; }
        var rec = best('record');
        if (rec) rec.en.handler(rec.hits[0], e, rec.hits);
        var ref = best('reference');
        if (ref) ref.en.handler(ref.hits[0] || null, e, ref.hits);
    }
    var clicks = {
        register: function (en) {
            if (!en || !en.id || !en.kind || typeof en.handler !== 'function') {
                throw new Error('LSTools.clicks.register: id, kind and handler are required');
            }
            clicks.unregister(en.id);
            entries.push(en);
        },
        unregister: function (id) {
            entries = entries.filter(function (x) { return x.id !== id; });
        },
        entries: function () { return entries.slice(); }
    };

    // ---- Escape router --------------------------------------------------
    function isEditable(t) {
        if (!t || !t.tagName) return false;
        var tag = t.tagName.toLowerCase();
        return tag === 'input' || tag === 'textarea' || tag === 'select' || !!t.isContentEditable;
    }
    function onKey(e) {
        if (e.key !== 'Escape' || e.defaultPrevented) return;
        if (isEditable(e.target)) return;      // typing a note is not cancelling a tool
        if (!activeId) return;
        var h = reg[activeId];
        if (h && typeof h.cancel === 'function') h.cancel();
    }

    // ---- pointer-capture drag -------------------------------------------
    function drag(el, o) {
        o = o || {};
        var pid = null, sx = 0, sy = 0;
        function end(e, cancelled) {
            if (pid === null) return;
            if (e && e.pointerId !== undefined && e.pointerId !== pid) return;
            var id = pid; pid = null;
            // Throws if capture was already lost (it is, after the implicit
            // release that follows a pointerup); harmless either way.
            try { el.releasePointerCapture(id); } catch (err) {}
            if (o.onEnd) o.onEnd(e, { cancelled: !!cancelled, startX: sx, startY: sy });
        }
        function down(e) {
            if (pid !== null) return;
            if (o.onStart && o.onStart(e) === false) return;   // "not a drag"
            pid = e.pointerId; sx = e.clientX; sy = e.clientY;
            try { el.setPointerCapture(pid); } catch (err) {}
            e.preventDefault();
        }
        function move(e) {
            if (pid === null || e.pointerId !== pid) return;
            if (o.onMove) o.onMove(e);
        }
        function up(e) { end(e, false); }
        function cancel(e) { end(e, true); }
        el.addEventListener('pointerdown', down);
        el.addEventListener('pointermove', move);
        el.addEventListener('pointerup', up);
        el.addEventListener('pointercancel', cancel);
        el.addEventListener('lostpointercapture', cancel);
        return {
            active: function () { return pid !== null; },
            cancel: function () { end(null, true); },
            destroy: function () {
                el.removeEventListener('pointerdown', down);
                el.removeEventListener('pointermove', move);
                el.removeEventListener('pointerup', up);
                el.removeEventListener('pointercancel', cancel);
                el.removeEventListener('lostpointercapture', cancel);
            }
        };
    }

    // ---- public ---------------------------------------------------------
    return {
        // Bind to a map. Attaches the tool group, the one click listener and
        // the Escape router. Idempotent for the same map.
        // opts.position: the control corner for the tool group (default
        // 'top-left'; /lidar/ uses 'top-right', its panel covers the left).
        init: function (m, opts) {
            if (map && map !== m) throw new Error('LSTools.init: already bound to a different map');
            if (map) return;
            map = m;
            if (opts && opts.position) position = opts.position;
            attach();
            map.on('click', dispatch);
            document.addEventListener('keydown', onKey);
        },
        // A mode button: selecting it releases whatever else was live.
        mode: function (o) {
            var b = mkBtn(o);
            b.setAttribute('role', 'radio');
            b.setAttribute('aria-checked', 'false');
            reg[o.id] = { onRelease: o.onRelease, canRelease: o.canRelease,
                          cancel: o.cancel, btn: b };
            b.addEventListener('click', function () {
                if (activeId === o.id) { o.onRelease(); }
                else { o.onSelect(); }
            });
            place(b, o.order);
            return b;
        },
        // A holder with no button (the reviser). claim/release/Escape treat
        // it exactly like a mode.
        hold: function (o) {
            reg[o.id] = { onRelease: o.onRelease, canRelease: o.canRelease,
                          cancel: o.cancel, btn: null };
        },
        // A plain action (measure's clear) — never becomes the active mode.
        action: function (o) {
            var b = mkBtn(o);
            b.addEventListener('click', o.onClick);
            place(b, o.order);
            return b;
        },
        // Claim the map. Returns false if the outgoing tool objected, in
        // which case the caller must not proceed.
        claim: function (id) {
            if (activeId === id) return true;
            if (activeId) {
                var cur = reg[activeId];
                if (cur && cur.canRelease && !cur.canRelease()) return false;
                var prev = activeId;
                activeId = null;          // before onRelease, so its own
                if (cur) cur.onRelease(); // release() call is a no-op
                setBtn(prev, false);
            }
            activeId = id;
            setBtn(id, true);
            popup.close();                // a tool taking the map dismisses
            return true;                  // whatever popup was open
        },
        // Give the map back. No-op unless `id` currently holds it, so a
        // tool's own deactivate() can call this unconditionally.
        release: function (id) {
            if (activeId !== id) return;
            activeId = null;
            setBtn(id, false);
        },
        active: function () { return activeId; },
        // THE guard. Replaces every hand-typed flag disjunction.
        blocked: function () { return activeId !== null; },
        // Diagnostics only: the bound map, for querying layers and sources
        // from the console or a test harness. map.js keeps its map closure-
        // local, and an hour was once lost proving from outside that a
        // symbol layer had not been added. Read-only by convention.
        map: function () { return map; },
        // Kept so nothing that still calls it breaks; init() does this.
        attach: attach,
        cursor: cursor,
        popup: popup,
        clicks: clicks,
        drag: drag
    };
})();
