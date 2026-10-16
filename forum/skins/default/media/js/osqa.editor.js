/*
 * The post editor: a Markdown text box (textarea#editor) with a toolbar
 * (compose/toolbar.html), keyboard shortcuts, its own undo history, link and
 * image dialogs, the live preview (rendered by the server, markdown_preview)
 * and the character/word count.
 *
 * - Toolbar (role=toolbar): one tab stop; Left/Right/Home/End move between
 *   the buttons; buttons that show a state (bold, italic, code, quote, lists,
 *   heading) have aria-pressed; tooltips name the shortcut. The code block
 *   button opens a menu of languages (```lang). Pointer clicks keep the focus
 *   in the text; keyboard activation keeps it on the button.
 * - Shortcuts (Cmd on a Mac): Ctrl+B bold, Ctrl+I italic, Ctrl+K link,
 *   Ctrl+E inline code, Ctrl+Z / Ctrl+Y / Ctrl+Shift+Z undo and redo,
 *   Ctrl+Enter posts (osqa.compose.js), Ctrl+Shift+V pastes plain text
 *   (osqa.paste.js).
 * - Tab / Shift+Tab indent and outdent list items, code blocks and selections
 *   of several lines; elsewhere Tab leaves the text box as usual. Escape,
 *   then Tab, always leaves it.
 * - Enter continues a list or quote (twice ends it) and keeps the indentation
 *   of code.
 *
 * Every change goes through Editor.apply(), which records it for undo and
 * fires an "input" event (marked with event.osqaEditor) so the drafts, the
 * preview, the count and the leave warning see it. textarea.osqaEditor is the
 * editor, for osqa.paste.js (pasted HTML, uploads).
 */
(function () {
    'use strict';

    var isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);
    var FENCE = /^ {0,3}(`{3,}|~{3,})[ \t]*([\w+#.-]*)[ \t]*$/;
    var LIST_ITEM = /^(\s*)([-*+]|(\d{1,9})([.)]))([ \t]+)/;
    var QUOTE = /^(\s*>[ \t]?)+/;
    var HEADING = /^(#{1,6})[ \t]+/;
    var INDENT = '    ';
    var HISTORY_SIZE = 200;

    var INLINE = {
        bold: {marker: '**', find: [/\*\*(?=\S)([^\n]*?\S)\*\*/g, /__(?=\S)([^\n]*?\S)__/g]},
        italic: {marker: '*', find: [/(?<![*\\\w])\*(?![\s*])([^\n*]*?[^\s*\\])?\*(?![*\w])/g,
                                     /(?<![_\\\w])_(?![\s_])([^\n_]*?[^\s_\\])?_(?![_\w])/g]},
        code: {marker: '`', find: [/(?<!`)(`+)(?!`)([^\n]*?[^`\n])\1(?!`)/g]}
    };

    function msg(key, fallback) {
        return (window.messages && window.messages[key]) || fallback;
    }

    function csrfToken() {
        var meta = document.querySelector('meta[name=csrf-token]');
        var input = document.querySelector('input[name=csrfmiddlewaretoken]');
        return meta ? meta.content : input ? input.value : '';
    }

    // ---- Text helpers ------------------------------------------------------

    function lineStart(v, i) {
        return v.lastIndexOf('\n', i - 1) + 1;
    }

    function lineEnd(v, i) {
        var n = v.indexOf('\n', i);
        return n < 0 ? v.length : n;
    }

    // The whole lines touched by the selection [s, e).
    function linesOf(v, s, e) {
        var end = e > s && v.charAt(e - 1) === '\n' ? e - 1 : e;
        var a = lineStart(v, s), b = lineEnd(v, end);
        return {start: a, end: b, lines: v.slice(a, b).split('\n')};
    }

    // The fenced code block around position i, if any.
    function fenceAt(v, i) {
        var lines = v.split('\n'), pos = 0, open = null;
        for (var n = 0; n < lines.length; n++) {
            var line = lines[n], start = pos, end = pos + line.length;
            pos = end + 1;
            if (!open) {
                var m = line.match(FENCE);
                if (m) open = {start: start, end: end, fence: m[1], lang: m[2], line: line};
            } else {
                var t = line.trim();
                if (t.length >= open.fence.length && t === new Array(t.length + 1).join(open.fence.charAt(0))) {
                    if (i >= open.start && i <= end) return open;
                    open = null;
                    continue;
                }
            }
            if (open && i >= open.start && i <= end && n === lines.length - 1) return open;
            if (start > i && !open) return null;
        }
        return open && i >= open.start ? open : null;
    }

    function inlineSpanAt(v, s, e, name) {
        var a = lineStart(v, s), b = lineEnd(v, e), line = v.slice(a, b);
        var spec = INLINE[name];
        for (var k = 0; k < spec.find.length; k++) {
            var re = new RegExp(spec.find[k].source, 'g'), m;
            while ((m = re.exec(line))) {
                var start = a + m.index, end = start + m[0].length;
                var open = name === 'code' ? m[1].length : spec.marker.length;
                if (name === 'italic') open = 1;
                if (start <= s && e <= end) {
                    // Inside "**bold**" the italic pattern must not see "*bold*".
                    return {start: start, end: end, open: open, close: open};
                }
                if (m[0].length === 0) re.lastIndex++;
            }
        }
        return null;
    }

    function isBlank(line) {
        return !line || !line.trim();
    }

    // ---- Undo history ------------------------------------------------------

    function History(editor) {
        this.editor = editor;
        this.stack = [editor.state()];
        this.index = 0;
        this.typing = false;
        this.timer = null;
    }

    History.prototype = {
        top: function () {
            return this.stack[this.index];
        },
        push: function (state) {
            var top = this.top();
            if (top && top.value === state.value) {
                top.start = state.start;
                top.end = state.end;
                return;
            }
            this.stack.length = this.index + 1;
            this.stack.push(state);
            if (this.stack.length > HISTORY_SIZE) this.stack.shift();
            this.index = this.stack.length - 1;
            this.editor.updateHistoryButtons();
        },
        sync: function () {
            // Before a change: the current text (and caret) is a step to come back to.
            if (this.typing) this.endTyping();
            this.push(this.editor.state());
        },
        beforeTyping: function () {
            if (!this.typing) {
                this.push(this.editor.state());
                this.typing = true;
            }
        },
        typed: function (inputType) {
            if (!this.typing) {
                // A change made by a script (draft restored, a test filling the box).
                this.push(this.editor.state());
                return;
            }
            clearTimeout(this.timer);
            if (/^(insertParagraph|insertLineBreak|insertFromPaste|insertFromDrop|deleteByCut)$/.test(inputType || '')) {
                this.endTyping();
            } else {
                var self = this;
                this.timer = setTimeout(function () { self.endTyping(); }, 700);
                this.editor.updateHistoryButtons();
            }
        },
        endTyping: function () {
            clearTimeout(this.timer);
            this.typing = false;
            this.push(this.editor.state());
        },
        canUndo: function () {
            return this.index > 0 || this.editor.textarea.value !== this.top().value;
        },
        canRedo: function () {
            return this.index < this.stack.length - 1;
        },
        undo: function () {
            if (this.typing) this.endTyping();
            if (this.editor.textarea.value !== this.top().value) this.push(this.editor.state());
            if (this.index > 0) {
                this.index--;
                this.editor.restore(this.top());
            }
        },
        redo: function () {
            if (this.typing) this.endTyping();
            if (this.canRedo()) {
                this.index++;
                this.editor.restore(this.top());
            }
        }
    };

    // ---- The editor --------------------------------------------------------

    function Editor(textarea, toolbar) {
        this.textarea = textarea;
        this.toolbar = toolbar;
        this.escaped = false;
        this.history = new History(this);
        textarea.osqaEditor = this;
    }

    Editor.prototype.state = function () {
        var ta = this.textarea;
        return {value: ta.value, start: ta.selectionStart, end: ta.selectionEnd};
    };

    Editor.prototype.notify = function () {
        var event = new Event('input', {bubbles: true});
        event.osqaEditor = true;
        this.textarea.dispatchEvent(event);
        this.updateButtons();
    };

    // Puts `value` in the text box (changing only the part that differs, so
    // the scroll position stays) and selects [start, end).
    Editor.prototype.write = function (value, start, end) {
        var ta = this.textarea, old = ta.value;
        if (old !== value) {
            var a = 0, max = Math.min(old.length, value.length);
            while (a < max && old.charCodeAt(a) === value.charCodeAt(a)) a++;
            var b = 0;
            while (b < max - a && old.charCodeAt(old.length - 1 - b) === value.charCodeAt(value.length - 1 - b)) b++;
            ta.setRangeText(value.slice(a, value.length - b), a, old.length - b, 'preserve');
        }
        ta.setSelectionRange(start, end === undefined ? start : end);
    };

    // A change: recorded for undo, then announced.
    Editor.prototype.apply = function (value, start, end) {
        this.history.sync();
        this.write(value, start, end);
        this.history.push(this.state());
        this.notify();
    };

    Editor.prototype.restore = function (state) {
        this.write(state.value, state.start, state.end);
        this.notify();
    };

    // Replaces the selection with `text` and selects [selStart, selEnd) of it
    // (default: the caret after it).
    Editor.prototype.replaceSelection = function (text, selStart, selEnd) {
        var ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        var value = v.slice(0, s) + text + v.slice(e);
        if (selStart === undefined) selStart = selEnd = text.length;
        this.apply(value, s + selStart, s + (selEnd === undefined ? selStart : selEnd));
    };

    // Inserts a block (a table, an image...) on lines of its own, with blank lines around it.
    Editor.prototype.insertBlock = function (text, selStart, selEnd) {
        var ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        if (s === e && s > lineStart(v, s)) {
            s = e = lineEnd(v, s);
        }
        var before = v.slice(0, s), after = v.slice(e);
        var pre = before && !/\n\n$/.test(before) ? (/\n$/.test(before) ? '\n' : '\n\n') : '';
        var post = !after ? '\n' : /^\n\n/.test(after) || after === '\n' ? '' : /^\n/.test(after) ? '\n' : '\n\n';
        var value = before + pre + text + post + after;
        var at = before.length + pre.length;
        if (selStart === undefined) selStart = selEnd = text.length;
        this.apply(value, at + selStart, at + (selEnd === undefined ? selStart : selEnd));
    };

    Editor.prototype.placeholder = function (command, fallback) {
        var button = this.button(command);
        return (button && button.getAttribute('data-placeholder')) || fallback;
    };

    Editor.prototype.button = function (command) {
        return this.toolbar ? this.toolbar.querySelector('[data-command="' + command + '"]') : null;
    };

    // ---- Commands ------------------------------------------------------------

    Editor.prototype.toggleInline = function (name) {
        var ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        var span = inlineSpanAt(v, s, e, name);
        if (span) {
            var inner = v.slice(span.start + span.open, span.end - span.close);
            var value = v.slice(0, span.start) + inner + v.slice(span.end);
            var clamp = function (p) {
                return Math.max(span.start, Math.min(span.start + inner.length, p - span.open));
            };
            this.apply(value, clamp(s), clamp(e));
            return;
        }
        var selected = v.slice(s, e);
        if (name === 'code' && /\n/.test(selected)) {
            this.codeBlock('');
            return;
        }
        var marker = INLINE[name].marker;
        var m = selected.match(/^(\s*)([\s\S]*?)(\s*)$/);
        var lead = m[1], core = m[2], trail = m[3];
        if (!core) {
            core = this.placeholder(name, name);
            lead = selected;
            trail = '';
        }
        var open = marker, close = marker;
        if (name === 'code' && /`/.test(core)) {
            open = '`` ';
            close = ' ``';
        }
        var text = lead + open + core + close + trail;
        this.replaceSelection(text, lead.length + open.length, lead.length + open.length + core.length);
    };

    // Adds or removes a prefix on every selected line (quote, lists).
    Editor.prototype.toggleLines = function (kind) {
        var ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        var block = linesOf(v, s, e), lines = block.lines;
        var content = lines.filter(function (l) { return !isBlank(l); });
        var test = {
            quote: function (l) { return QUOTE.test(l); },
            ulist: function (l) { var m = l.match(LIST_ITEM); return m && !m[3]; },
            olist: function (l) { var m = l.match(LIST_ITEM); return m && !!m[3]; }
        }[kind];
        var all = content.length > 0 && content.every(test);
        var number = 1, out;
        if (all) {
            out = lines.map(function (l) {
                return kind === 'quote' ? l.replace(/^(\s*)>[ \t]?/, '$1') : l.replace(LIST_ITEM, '$1');
            });
        } else {
            out = lines.map(function (l) {
                if (isBlank(l) && lines.length > 1) return kind === 'quote' ? '>' : l;
                if (kind === 'quote') return '> ' + l;
                var m = l.match(LIST_ITEM), indent = (l.match(/^\s*/) || [''])[0];
                var rest = m ? l.slice(m[0].length) : l.slice(indent.length);
                return indent + (kind === 'ulist' ? '- ' : (number++) + '. ') + rest;
            });
        }
        var text = out.join('\n');
        var empty = !all && content.length === 0;
        var placeholderText = '';
        if (empty && kind !== 'quote') {
            placeholderText = this.placeholder(kind, 'List item');
            text += placeholderText;
        }
        // A list or quote needs a blank line before it (and after it, or the
        // next paragraph joins its last item).
        var pre = '', post = '';
        if (!all) {
            var prevLine = v.slice(lineStart(v, Math.max(0, block.start - 1)), Math.max(0, block.start - 1));
            if (block.start > 0 && !isBlank(prevLine) && !test(prevLine)) pre = '\n';
            var nextStart = block.end + 1, nextLine = v.slice(nextStart, lineEnd(v, nextStart));
            if (block.end < v.length && !isBlank(nextLine) && !test(nextLine)) post = '\n';
        }
        var value = v.slice(0, block.start) + pre + text + post + v.slice(block.end);
        var start = block.start + pre.length, end = start + text.length;
        if (placeholderText) {
            this.apply(value, end - placeholderText.length, end);
        } else if (s === e) {
            var fromEnd = block.end - s;
            this.apply(value, Math.max(start, end - fromEnd));
        } else {
            this.apply(value, start, end);
        }
    };

    Editor.prototype.heading = function () {
        var ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        var a = lineStart(v, s), b = lineEnd(v, s), line = v.slice(a, b);
        var m = line.match(HEADING), rest = m ? line.slice(m[0].length) : line, prefix;
        if (!m) prefix = '## ';
        else if (m[1] === '##') prefix = '### ';
        else prefix = '';
        var placeholderText = '';
        if (prefix && !rest.trim()) {
            placeholderText = this.placeholder('heading', 'Heading');
            rest = placeholderText;
        }
        var text = prefix + rest;
        var value = v.slice(0, a) + text + v.slice(b);
        if (placeholderText) {
            this.apply(value, a + prefix.length, a + text.length);
        } else {
            var shift = text.length - line.length;
            this.apply(value, Math.max(a, s + shift), Math.max(a, e + shift));
        }
    };

    Editor.prototype.codeBlock = function (lang) {
        var ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        var fence = fenceAt(v, s);
        if (fence) {
            // Inside a code block: change its language.
            var indent = fence.line.match(/^\s*/)[0];
            var line = indent + fence.fence + (lang || '');
            var shift = line.length - (fence.end - fence.start);
            this.apply(v.slice(0, fence.start) + line + v.slice(fence.end),
                       s > fence.end ? s + shift : fence.start + line.length,
                       e > fence.end ? e + shift : fence.start + line.length);
            return;
        }
        var code = '';
        if (s !== e) {
            var block = linesOf(v, s, e);
            code = v.slice(block.start, block.end);
            ta.setSelectionRange(block.start, block.end);
        }
        var longest = 2;
        (code.match(/^\s*`{3,}/gm) || []).forEach(function (run) { longest = Math.max(longest, run.trim().length); });
        var marks = new Array(longest + 2).join('`');
        var body = code || this.placeholder('codeblock', 'code');
        var open = marks + (lang || '') + '\n';
        this.insertBlock(open + body + '\n' + marks, open.length, open.length + body.length);
    };

    Editor.prototype.table = function () {
        var header = this.placeholder('table', 'Column');
        var cell = this.button('table') && this.button('table').getAttribute('data-cell') || 'Cell';
        var h1 = header + ' 1', h2 = header + ' 2';
        var width = Math.max(h1.length, h2.length, cell.length, 3);
        var pad = function (t) { return t + new Array(width - t.length + 1).join(' '); };
        var dash = new Array(width + 1).join('-');
        var text = '| ' + pad(h1) + ' | ' + pad(h2) + ' |\n' +
                   '| ' + dash + ' | ' + dash + ' |\n' +
                   '| ' + pad(cell) + ' | ' + pad(cell) + ' |';
        this.insertBlock(text, 2, 2 + h1.length);
    };

    // Tab and Shift+Tab: indent or outdent list items, code and several lines.
    Editor.prototype.indent = function (outdent) {
        var ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        var block = linesOf(v, s, e);
        var inCode = !!fenceAt(v, s);
        var inList = block.lines.every(function (l) { return isBlank(l) || LIST_ITEM.test(l); }) &&
                     block.lines.some(function (l) { return LIST_ITEM.test(l); });
        var several = block.lines.length > 1;
        if (!inCode && !inList && !several) return false;
        if (inCode && !several && !outdent && !inList) {
            this.replaceSelection(INDENT);
            return true;
        }
        var removedFirst = 0, total = 0;
        var out = block.lines.map(function (l, n) {
            if (outdent) {
                var m = l.match(/^( {1,4}|\t)/), cut = m ? m[0].length : 0;
                if (n === 0) removedFirst = cut;
                total += cut;
                return l.slice(cut);
            }
            if (isBlank(l) && several) return l;
            if (n === 0) removedFirst = -INDENT.length;
            total -= INDENT.length;
            return INDENT + l;
        });
        var text = out.join('\n');
        if (text === block.lines.join('\n')) return true;
        var value = v.slice(0, block.start) + text + v.slice(block.end);
        if (s === e) {
            var at = Math.max(block.start, s - removedFirst);
            this.apply(value, at);
        } else {
            this.apply(value, block.start, block.start + text.length);
        }
        return true;
    };

    // Enter: continue a list or quote, end it on an empty item, keep code indentation.
    Editor.prototype.newline = function () {
        var ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        if (s !== e) return false;
        var a = lineStart(v, s), b = lineEnd(v, s), line = v.slice(a, b), before = v.slice(a, s);
        if (fenceAt(v, s) && a !== fenceAt(v, s).start) {
            var indent = before.match(/^[ \t]*/)[0];
            if (!indent) return false;
            this.replaceSelection('\n' + indent);
            return true;
        }
        var m = line.match(LIST_ITEM), q = line.match(QUOTE);
        var prefix = m ? m[0] : q ? q[0] : '';
        if (!prefix || s < a + prefix.length) return false;
        var task = m && line.slice(prefix.length).match(/^\[[ xX]\][ \t]+/);
        var full = prefix + (task ? task[0] : '');
        if (!line.slice(full.length).trim() && s === b) {
            // An empty item: end the list (or quote).
            this.apply(v.slice(0, a) + v.slice(b), a);
            return true;
        }
        var next = prefix;
        if (m && m[3]) {
            next = m[1] + (parseInt(m[3], 10) + 1) + m[4] + m[5];
        }
        if (task) next += '[ ] ';
        this.replaceSelection('\n' + next);
        return true;
    };

    // Which formats the selection is in (for aria-pressed).
    Editor.prototype.formats = function () {
        var ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        var block = linesOf(v, s, e), content = block.lines.filter(function (l) { return !isBlank(l); });
        var every = function (test) { return content.length > 0 && content.every(test); };
        var line = v.slice(lineStart(v, s), lineEnd(v, s));
        return {
            bold: !!inlineSpanAt(v, s, e, 'bold'),
            italic: !!inlineSpanAt(v, s, e, 'italic'),
            code: !!inlineSpanAt(v, s, e, 'code'),
            quote: every(function (l) { return QUOTE.test(l); }),
            ulist: every(function (l) { var m = l.match(LIST_ITEM); return m && !m[3]; }),
            olist: every(function (l) { var m = l.match(LIST_ITEM); return m && !!m[3]; }),
            heading: HEADING.test(line)
        };
    };

    Editor.prototype.updateButtons = function () {
        if (!this.toolbar) return;
        var formats = this.formats();
        Object.keys(formats).forEach(function (name) {
            var button = this.button(name);
            if (button) button.setAttribute('aria-pressed', formats[name] ? 'true' : 'false');
        }, this);
        this.updateHistoryButtons();
    };

    Editor.prototype.updateHistoryButtons = function () {
        if (!this.toolbar || !this.history) return;
        var undo = this.button('undo'), redo = this.button('redo');
        if (undo) undo.setAttribute('aria-disabled', this.history.canUndo() ? 'false' : 'true');
        if (redo) redo.setAttribute('aria-disabled', this.history.canRedo() ? 'false' : 'true');
    };

    Editor.prototype.run = function (command, arg) {
        switch (command) {
        case 'bold':
        case 'italic':
        case 'code':
            this.toggleInline(command);
            break;
        case 'quote':
        case 'ulist':
        case 'olist':
            this.toggleLines(command);
            break;
        case 'heading':
            this.heading();
            break;
        case 'codeblock':
            this.codeBlock(arg);
            break;
        case 'table':
            this.table();
            break;
        case 'link':
            this.linkDialog();
            break;
        case 'image':
            this.imageDialog();
            break;
        case 'undo':
            this.history.undo();
            break;
        case 'redo':
            this.history.redo();
            break;
        default:
            return false;
        }
        return true;
    };

    // ---- Link and image dialogs ----------------------------------------------

    function linkSpanAt(v, s, e, image) {
        var a = lineStart(v, s), line = v.slice(a, lineEnd(v, e));
        var re = /(!?)\[([^\]\n]*)\]\(([^)\s]*)(?:\s+"([^"\n]*)")?\)/g, m;
        while ((m = re.exec(line))) {
            var start = a + m.index, end = start + m[0].length;
            if (!!m[1] === image && start <= s && e <= end) {
                return {start: start, end: end, text: m[2], url: m[3], title: m[4] || ''};
            }
        }
        return null;
    }

    function cleanUrl(url) {
        url = url.trim();
        if (url && !/^([a-z][\w+.-]*:|\/|#|\.\.?\/)/i.test(url) && /^[\w-]+(\.[\w-]+)+/.test(url)) {
            url = 'https://' + url;
        }
        return url.replace(/[ ()<>]/g, function (c) {
            return {' ': '%20', '(': '%28', ')': '%29', '<': '%3C', '>': '%3E'}[c];
        });
    }

    function escapeLabel(text) {
        return text.replace(/([\[\]])/g, '\\$1');
    }

    Editor.prototype.openDialog = function (dialog, onOk) {
        // Never left open from an earlier use (before this use's listeners).
        if (dialog.open) dialog.close();
        var editor = this, ta = this.textarea;
        var range = [ta.selectionStart, ta.selectionEnd];
        var returnTo = document.activeElement && document.activeElement !== document.body ? document.activeElement : ta;
        var ok = dialog.querySelector('[data-dialog-ok]'), cancel = dialog.querySelector('[data-dialog-cancel]');
        var error = dialog.querySelector('.editor-dialog-error');
        if (error) {
            error.hidden = true;
            error.textContent = '';
        }
        function close() {
            ok.removeEventListener('click', confirm);
            cancel.removeEventListener('click', dismiss);
            dialog.removeEventListener('keydown', keys);
            dialog.removeEventListener('close', closed);
            if (dialog.open) dialog.close();
            if (!returnTo.isConnected) returnTo = ta;
            returnTo.focus({preventScroll: true});
        }
        function confirm() {
            if (ok.getAttribute('aria-disabled') === 'true') return;
            ta.setSelectionRange(range[0], range[1]);
            var problem = onOk();
            if (problem) {
                if (error) {
                    error.textContent = problem;
                    error.hidden = false;
                }
                return;
            }
            close();
        }
        function dismiss() {
            close();
            ta.setSelectionRange(range[0], range[1]);
        }
        function keys(e) {
            if (e.key === 'Enter' && e.target.tagName === 'INPUT' && e.target.type !== 'file') {
                e.preventDefault();
                confirm();
            }
        }
        function closed() {
            // Escape (the dialog's cancel) closes it.
            close();
        }
        ok.addEventListener('click', confirm);
        cancel.addEventListener('click', dismiss);
        dialog.addEventListener('keydown', keys);
        dialog.addEventListener('close', closed);
        // A modal the browser refuses to open still opens, as a plain dialog.
        try {
            dialog.showModal();
        } catch (err) {
            dialog.show();
        }
        var first = dialog.querySelector('[autofocus]') || dialog.querySelector('input');
        if (first) first.focus();
        editor.hideTooltip && editor.hideTooltip();
    };

    Editor.prototype.linkDialog = function () {
        var dialog = document.getElementById('editor-link-dialog');
        if (!dialog) return;
        var editor = this, ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        var span = linkSpanAt(v, s, e, false);
        var url = dialog.querySelector('#editor-link-url'), text = dialog.querySelector('#editor-link-text');
        var selected = v.slice(s, e);
        if (span) {
            url.value = span.url;
            text.value = span.text.replace(/\\([\[\]])/g, '$1');
        } else if (/^\s*(https?:\/\/|www\.)\S+\s*$/.test(selected)) {
            url.value = selected.trim();
            text.value = '';
        } else {
            url.value = '';
            text.value = selected.trim();
        }
        url.removeAttribute('autofocus');
        text.removeAttribute('autofocus');
        (url.value ? text : url).setAttribute('autofocus', '');
        this.openDialog(dialog, function () {
            var href = cleanUrl(url.value);
            if (!href) return dialog.getAttribute('data-empty-error') || 'Enter a web address.';
            var label = escapeLabel(text.value.trim() || url.value.trim());
            var md = '[' + label + '](' + href + (span && span.title ? ' "' + span.title + '"' : '') + ')';
            if (span) ta.setSelectionRange(span.start, span.end);
            editor.replaceSelection(md);
            return null;
        });
    };

    Editor.prototype.imageDialog = function () {
        var dialog = document.getElementById('editor-image-dialog');
        if (!dialog) return;
        var editor = this, ta = this.textarea, v = ta.value, s = ta.selectionStart, e = ta.selectionEnd;
        var span = linkSpanAt(v, s, e, true);
        var url = dialog.querySelector('#image-url'), alt = dialog.querySelector('#image-alt');
        var file = dialog.querySelector('#file-upload'), status = dialog.querySelector('.editor-upload-status');
        url.value = span ? span.url : '';
        alt.value = span ? span.text : v.slice(s, e).trim();
        file.value = '';
        status.textContent = '';
        status.className = 'editor-upload-status';
        status.setAttribute('role', 'status');
        this.openDialog(dialog, function () {
            var src = cleanUrl(url.value);
            if (!src) return dialog.getAttribute('data-empty-error') || 'Choose an image or enter its address.';
            var md = '![' + escapeLabel(alt.value.trim()) + '](' + src + (span && span.title ? ' "' + span.title + '"' : '') + ')';
            if (span) ta.setSelectionRange(span.start, span.end);
            editor.replaceSelection(md);
            return null;
        });
    };

    // The image dialog's file input: upload at once, the address goes into the dialog.
    function setupImageUpload() {
        var dialog = document.getElementById('editor-image-dialog');
        if (!dialog) return;
        var file = dialog.querySelector('#file-upload'), url = dialog.querySelector('#image-url');
        var alt = dialog.querySelector('#image-alt'), status = dialog.querySelector('.editor-upload-status');
        var controls = dialog.querySelectorAll('button, input');
        function busy(on) {
            controls.forEach(function (c) { c.disabled = on; });
            dialog.setAttribute('aria-busy', on ? 'true' : 'false');
        }
        function say(text, isError) {
            status.textContent = text;
            status.className = 'editor-upload-status' + (isError ? ' upload-error' : '');
            status.setAttribute('role', isError ? 'alert' : 'status');
        }
        file.addEventListener('change', function () {
            var chosen = file.files && file.files[0];
            if (!chosen) return;
            busy(true);
            say(msg('uploading', 'Uploading') + ' ' + chosen.name + '…', false);
            uploadFile(chosen).then(function (reply) {
                busy(false);
                if (reply.url) {
                    url.value = reply.url;
                    if (!alt.value.trim()) alt.value = altFromName(chosen.name);
                    say('', false);
                    alt.focus();
                } else {
                    say(reply.error || msg('upload_failed', 'The image could not be uploaded.'), true);
                    file.value = '';
                    file.focus();
                }
            });
        });
    }

    function altFromName(name) {
        return (name || '').replace(/\.[^.]+$/, '').replace(/[-_]+/g, ' ').replace(/[\[\]]/g, '').trim();
    }

    // Uploads one image; resolves to the view's JSON reply ({url} or {error}).
    function uploadFile(file) {
        var data = new FormData();
        data.append('file-upload', file, file.name || 'image.png');
        return fetch(msg('upload_url', '/upload/'), {
            method: 'POST',
            body: data,
            credentials: 'same-origin',
            headers: {'X-CSRFToken': csrfToken()}
        }).then(function (response) {
            return response.json().catch(function () { return {}; });
        }).catch(function () {
            return {};
        });
    }

    // ---- Toolbar: roving tab stop, menus, tooltips --------------------------

    function shortcutLabel(key, shift) {
        return isMac ? (shift ? '⇧' : '') + '⌘' + key : 'Ctrl+' + (shift ? 'Shift+' : '') + key;
    }

    function setupToolbar(editor) {
        var toolbar = editor.toolbar, ta = editor.textarea;
        var items = Array.prototype.slice.call(toolbar.querySelectorAll('.editor-button'));
        var tooltip = document.createElement('div');
        tooltip.className = 'editor-tooltip';
        tooltip.id = 'editor-tooltip';
        tooltip.setAttribute('role', 'tooltip');
        tooltip.hidden = true;
        document.body.appendChild(tooltip);
        var current = items[0];

        items.forEach(function (item) {
            item.tabIndex = item === current ? 0 : -1;
            var key = item.getAttribute('data-key');
            if (key) {
                var shift = item.hasAttribute('data-shift');
                item.setAttribute('aria-keyshortcuts', (isMac ? 'Meta+' : 'Control+') + (shift ? 'Shift+' : '') + key);
                item.setAttribute('data-tip', item.getAttribute('aria-label') + ' (' + shortcutLabel(key, shift) + ')');
            } else {
                item.setAttribute('data-tip', item.getAttribute('aria-label'));
            }
        });
        // Redo is Ctrl+Y on Windows/Linux, Cmd+Shift+Z on a Mac.
        var redo = editor.button('redo');
        if (redo && isMac) {
            redo.setAttribute('aria-keyshortcuts', 'Meta+Shift+Z');
            redo.setAttribute('data-tip', redo.getAttribute('aria-label') + ' (' + shortcutLabel('Z', true) + ')');
        }

        function focusItem(item) {
            current.tabIndex = -1;
            current = item;
            item.tabIndex = 0;
            item.focus();
        }

        function showTip(item) {
            var text = item.getAttribute('data-tip');
            if (!text) return;
            tooltip.textContent = text;
            tooltip.hidden = false;
            var r = item.getBoundingClientRect(), w = tooltip.offsetWidth;
            var left = Math.max(4, Math.min(r.left + r.width / 2 - w / 2, document.documentElement.clientWidth - w - 4));
            tooltip.style.left = left + 'px';
            tooltip.style.top = (r.bottom + 6) + 'px';
        }
        function hideTip() {
            tooltip.hidden = true;
        }
        editor.hideTooltip = hideTip;

        toolbar.addEventListener('focusin', function (e) {
            var item = e.target.closest('.editor-button');
            if (!item) return;
            if (item !== current) {
                current.tabIndex = -1;
                current = item;
                item.tabIndex = 0;
            }
            if (item.matches(':focus-visible')) showTip(item);
        });
        toolbar.addEventListener('focusout', hideTip);
        toolbar.addEventListener('pointerover', function (e) {
            var item = e.target.closest('.editor-button');
            if (item && e.pointerType === 'mouse') showTip(item);
        });
        // The tip stays while the pointer moves onto it, and Escape hides it
        // wherever the focus is (WCAG 1.4.13).
        toolbar.addEventListener('pointerout', function (e) {
            if (!e.relatedTarget || !(toolbar.contains(e.relatedTarget) || tooltip.contains(e.relatedTarget))) hideTip();
        });
        tooltip.addEventListener('pointerleave', function (e) {
            if (!e.relatedTarget || !toolbar.contains(e.relatedTarget)) hideTip();
        });
        tooltip.addEventListener('pointerdown', hideTip);
        document.addEventListener('keydown', function (e) {
            if (e.key === 'Escape' && !tooltip.hidden) hideTip();
        });
        toolbar.addEventListener('scroll', hideTip, {passive: true});
        window.addEventListener('scroll', hideTip, {passive: true});

        toolbar.addEventListener('keydown', function (e) {
            var index = items.indexOf(current);
            var target = {ArrowRight: index + 1, ArrowLeft: index - 1, Home: 0, End: items.length - 1}[e.key];
            if (e.key === 'Escape') {
                hideTip();
                return;
            }
            if (target === undefined || e.altKey || e.ctrlKey || e.metaKey) return;
            e.preventDefault();
            focusItem(items[(target + items.length) % items.length]);
        });

        // A pointer click keeps the focus (and the selection) in the text box.
        toolbar.addEventListener('mousedown', function (e) {
            if (e.target.closest('button.editor-button')) e.preventDefault();
        });

        toolbar.addEventListener('click', function (e) {
            var item = e.target.closest('button.editor-button');
            if (!item) return;
            hideTip();
            if (item.getAttribute('aria-disabled') === 'true') return;
            var command = item.getAttribute('data-command');
            if (command === 'codeblock') {
                languageMenu.toggle(item, e.detail === 0);
                return;
            }
            var fromKeyboard = document.activeElement === item;
            if (!fromKeyboard && document.activeElement !== ta) ta.focus();
            editor.run(command);
        });

        var languageMenu = setupMenu(editor, document.getElementById('editor-languages'));
        var codeButton = editor.button('codeblock');
        if (codeButton) {
            codeButton.addEventListener('keydown', function (e) {
                if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
                    e.preventDefault();
                    languageMenu.open(codeButton, true, e.key === 'ArrowUp');
                }
            });
        }
    }

    // The code language menu (role=menu) of the code block button.
    function setupMenu(editor, menu) {
        var api = {open: function () {}, close: function () {}, toggle: function () {}};
        if (!menu) return api;
        var items = Array.prototype.slice.call(menu.querySelectorAll('[role=menuitem]'));
        var button = null, keyboard = false;
        items.forEach(function (i) { i.tabIndex = -1; });

        function place() {
            var host = menu.offsetParent || document.body;
            var r = button.getBoundingClientRect(), h = host.getBoundingClientRect();
            var width = menu.offsetWidth;
            var left = Math.max(0, Math.min(r.left - h.left, h.width - width));
            menu.style.left = left + 'px';
            menu.style.top = (r.bottom - h.top + 4) + 'px';
        }
        function open(from, withKeyboard, last) {
            button = from;
            keyboard = withKeyboard;
            menu.hidden = false;
            button.setAttribute('aria-expanded', 'true');
            place();
            var lang = (fenceAt(editor.textarea.value, editor.textarea.selectionStart) || {}).lang;
            var chosen = items.filter(function (i) { return lang !== undefined && i.getAttribute('data-language') === lang; })[0];
            (chosen || (last ? items[items.length - 1] : items[0])).focus();
            document.addEventListener('pointerdown', outside, true);
        }
        function close(refocus) {
            if (menu.hidden) return;
            menu.hidden = true;
            document.removeEventListener('pointerdown', outside, true);
            if (button) {
                button.setAttribute('aria-expanded', 'false');
                if (refocus === 'button') button.focus();
                else if (refocus === 'text') editor.textarea.focus();
            }
        }
        function outside(e) {
            if (!menu.contains(e.target) && e.target !== button && !button.contains(e.target)) close();
        }
        menu.addEventListener('keydown', function (e) {
            var index = items.indexOf(document.activeElement);
            var target = {ArrowDown: index + 1, ArrowUp: index - 1, Home: 0, End: items.length - 1}[e.key];
            if (target !== undefined) {
                e.preventDefault();
                items[(target + items.length) % items.length].focus();
            } else if (e.key === 'Escape') {
                e.preventDefault();
                e.stopPropagation();
                close('button');
            } else if (e.key === 'Tab') {
                close();
            } else if (e.key.length === 1 && /\S/.test(e.key)) {
                var key = e.key.toLowerCase();
                var match = items.slice(index + 1).concat(items.slice(0, index + 1)).filter(function (i) {
                    return i.textContent.trim().toLowerCase().indexOf(key) === 0;
                })[0];
                if (match) match.focus();
            }
        });
        menu.addEventListener('mousedown', function (e) { e.preventDefault(); });
        menu.addEventListener('click', function (e) {
            var item = e.target.closest('[role=menuitem]');
            if (!item) return;
            var wasKeyboard = keyboard && e.detail === 0;
            close(wasKeyboard ? 'button' : 'text');
            editor.run('codeblock', item.getAttribute('data-language'));
        });
        api.open = open;
        api.close = close;
        api.toggle = function (from, withKeyboard) {
            if (menu.hidden) open(from, withKeyboard);
            else close('button');
        };
        return api;
    }

    // ---- Keys in the text box ---------------------------------------------------

    function setupKeys(editor) {
        var ta = editor.textarea;
        ta.addEventListener('keydown', function (e) {
            if (e.isComposing || e.defaultPrevented) return;
            var escaped = editor.escaped;
            editor.escaped = false;
            var mod = isMac ? e.metaKey && !e.ctrlKey : e.ctrlKey && !e.metaKey;
            var key = e.key.length === 1 ? e.key.toLowerCase() : e.key;
            if (mod && !e.altKey) {
                var command = {b: 'bold', i: 'italic', k: 'link', e: 'code', z: e.shiftKey ? 'redo' : 'undo'}[key];
                if (key === 'y' && !isMac && !e.shiftKey) command = 'redo';
                if (command && (!e.shiftKey || key === 'z')) {
                    e.preventDefault();
                    editor.run(command);
                }
                return;
            }
            if (e.altKey || e.ctrlKey || e.metaKey) return;
            if (key === 'Escape') {
                editor.escaped = true;
                return;
            }
            if (key === 'Tab' && !escaped) {
                if (editor.indent(e.shiftKey)) e.preventDefault();
                return;
            }
            if (key === 'Enter' && !e.shiftKey) {
                if (editor.newline()) e.preventDefault();
            }
        });
        ta.addEventListener('beforeinput', function (e) {
            if (e.inputType === 'historyUndo' || e.inputType === 'historyRedo') {
                e.preventDefault();
                editor.run(e.inputType === 'historyUndo' ? 'undo' : 'redo');
                return;
            }
            editor.history.beforeTyping();
        });
        ta.addEventListener('input', function (e) {
            if (e.osqaEditor) return;
            editor.history.typed(e.inputType);
            editor.scheduleButtons();
        });
        var pending = false;
        editor.scheduleButtons = function () {
            if (pending) return;
            pending = true;
            requestAnimationFrame(function () {
                pending = false;
                editor.updateButtons();
            });
        };
        document.addEventListener('selectionchange', function () {
            if (document.activeElement === ta) editor.scheduleButtons();
        });
        ta.addEventListener('keyup', editor.scheduleButtons);
        ta.addEventListener('mouseup', editor.scheduleButtons);
    }

    // ---- Preview and counts ---------------------------------------------------

    function setupPreview(editor, preview) {
        var ta = editor.textarea, url = preview.getAttribute('data-preview-url');
        if (!url) return;
        var timer = null, last = null, controller = null, delay = 300;
        function show(html) {
            preview.innerHTML = html;
        }
        function render() {
            var text = ta.value;
            if (text === last) return;
            last = text;
            if (controller) controller.abort();
            controller = null;
            if (!text.trim()) {
                show('');
                return;
            }
            var mine = controller = window.AbortController ? new AbortController() : null;
            var started = Date.now();
            fetch(url, {
                method: 'POST',
                body: new URLSearchParams({text: text}),
                credentials: 'same-origin',
                headers: {'X-CSRFToken': csrfToken(), 'X-Requested-With': 'XMLHttpRequest'},
                signal: mine ? mine.signal : undefined
            }).then(function (response) {
                return response.ok ? response.text() : null;
            }).then(function (html) {
                // A slow server gets fewer requests.
                delay = Math.min(1500, Math.max(300, Date.now() - started));
                if (html !== null && controller === mine) show(html);
            }).catch(function () {});
        }
        ta.addEventListener('input', function () {
            clearTimeout(timer);
            timer = setTimeout(render, delay);
        });
        render();
    }

    function setupMetrics(editor) {
        var ta = editor.textarea;
        var count = document.getElementById('editor-metrics');
        var hint = document.getElementById('editor-min-hint');
        var min = hint ? parseInt(hint.getAttribute('data-min-length'), 10) || 0 : 0;
        if (!count && !hint) return;
        function update() {
            // Counted as the server does (runs of spaces count as one).
            var text = ta.value, length = text.replace(/ {2,}/g, ' ').length;
            var words = (text.match(/[\p{L}\p{N}]+(?:['\u2019.-][\p{L}\p{N}]+)*/gu) || []).length;
            if (count) {
                count.textContent = length + ' ' + (length === 1 ? msg('character', 'character') : msg('characters', 'characters')) +
                    ' · ' + words + ' ' + (words === 1 ? msg('word', 'word') : msg('words', 'words'));
            }
            if (hint) {
                hint.classList.toggle('is-met', length >= min);
                hint.classList.toggle('is-short', length > 0 && length < min);
            }
        }
        ta.addEventListener('input', update);
        update();
    }

    // ---- Start -------------------------------------------------------------------

    function setup() {
        var ta = document.getElementById('editor');
        if (!ta || ta.tagName !== 'TEXTAREA' || ta.osqaEditor) return;
        var toolbar = document.querySelector('.editor-toolbar[aria-controls="editor"]');
        var editor = new Editor(ta, toolbar);
        // The dialogs live outside the post form (no nested form fields, no implicit submission).
        ['editor-link-dialog', 'editor-image-dialog'].forEach(function (id) {
            var dialog = document.getElementById(id);
            if (dialog && dialog.parentNode !== document.body) document.body.appendChild(dialog);
        });
        var hint = document.getElementById('editor-keys-hint'), min = document.getElementById('editor-min-hint');
        var described = (ta.getAttribute('aria-describedby') || '').split(/\s+/).filter(Boolean);
        [min, hint].forEach(function (el) {
            if (el && described.indexOf(el.id) < 0) described.push(el.id);
        });
        if (described.length) ta.setAttribute('aria-describedby', described.join(' '));
        if (toolbar) setupToolbar(editor);
        setupKeys(editor);
        setupImageUpload();
        var preview = document.getElementById('previewer');
        if (preview) setupPreview(editor, preview);
        setupMetrics(editor);
        editor.updateButtons();
        ta.dispatchEvent(new CustomEvent('osqa:editorready', {bubbles: true, detail: editor}));
    }

    window.OsqaEditor = {
        uploadFile: uploadFile,
        altFromName: altFromName,
        fenceAt: fenceAt
    };

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setup);
    } else {
        setup();
    }
})();
