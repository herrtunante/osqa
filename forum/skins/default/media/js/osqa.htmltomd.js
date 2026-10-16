/*
 * HTML to Markdown for pasting into the post editor (osqa.paste.js).
 *
 * osqaHtmlToMarkdown(html) returns Markdown text: headings, paragraphs,
 * bold/italic (also Google Docs' styled spans), links (http, https, mailto
 * and relative only), inline code and code blocks (with their language),
 * lists (nested, Word's "mso-list" paragraphs too), block quotes, tables and
 * images with a web address. Everything else becomes plain text: no HTML is
 * ever written, and "<" in the text is written as "&lt;".
 *
 * The HTML is parsed with DOMParser, which runs no scripts and loads nothing.
 * osqaHtmlIsRich(html) says whether the HTML has any of the structure above
 * (otherwise the plain text of the clipboard is pasted as it is).
 */
(function () {
    var SKIP = /^(script|style|head|title|meta|link|noscript|template|iframe|object|embed|svg|canvas|button|input|select|textarea|video|audio|form)$/;
    var BLOCK = /^(address|article|aside|blockquote|dd|div|dl|dt|fieldset|figcaption|figure|footer|h[1-6]|header|hr|li|main|nav|ol|p|pre|section|table|tbody|td|tfoot|th|thead|tr|ul)$/;
    var RICH = 'a[href],b,strong,em,i,h1,h2,h3,h4,h5,h6,ul,ol,li,table,pre,code,blockquote,img[src],' +
               'span[style*="font-weight"],span[style*="italic"],p.MsoListParagraph,[style*="mso-list"]';

    function parse(html) {
        return new DOMParser().parseFromString(html || '', 'text/html');
    }

    function tag(node) {
        return node.nodeType === 1 ? node.nodeName.toLowerCase() : '';
    }

    function style(node, name) {
        var value = node.style && node.style.getPropertyValue(name);
        return (value || '').toLowerCase();
    }

    function isBold(node) {
        var t = tag(node), weight = style(node, 'font-weight');
        if (t === 'b' || t === 'strong') {
            // Google Docs wraps everything in <b style="font-weight:normal">.
            return !(weight === 'normal' || weight === '400');
        }
        return weight === 'bold' || weight === 'bolder' || parseInt(weight, 10) >= 600;
    }

    function isItalic(node) {
        var t = tag(node);
        if (t === 'em' || t === 'i' || t === 'cite' || t === 'var' || t === 'dfn') {
            return style(node, 'font-style') !== 'normal';
        }
        return style(node, 'font-style') === 'italic';
    }

    function isMonospace(node) {
        return /^(code|kbd|samp|tt)$/.test(tag(node));
    }

    function safeUrl(url) {
        url = (url || '').trim();
        if (/^(https?:|mailto:)/i.test(url) || /^(\/|#|\.\.?\/)/.test(url)) {
            return url.replace(/[()\s]/g, function (c) { return encodeURIComponent(c); });
        }
        return '';
    }

    // Characters that would turn plain text into Markdown or HTML.
    function escapeText(text) {
        return text.replace(/\\/g, '\\\\')
                   .replace(/([*_`\[\]])/g, '\\$1')
                   .replace(/</g, '&lt;');
    }

    function escapeLineStarts(text) {
        return text.replace(/^(\s*)([#>+-]|\d+[.)])(?=\s)/gm, function (m, space, mark) {
            return space + mark.replace(/^(\d+)([.)])$/, '$1\\$2').replace(/^([#>+-])$/, '\\$1');
        });
    }

    function codeSpan(text) {
        text = text.replace(/\s+/g, ' ');
        var longest = 0;
        (text.match(/`+/g) || []).forEach(function (run) { longest = Math.max(longest, run.length); });
        var fence = new Array(longest + 2).join('`');
        var pad = /^`|`$/.test(text) ? ' ' : '';
        return fence + pad + text + pad + fence;
    }

    function fenceFor(text) {
        var longest = 2;
        (text.match(/^`{3,}/gm) || []).forEach(function (run) { longest = Math.max(longest, run.length); });
        return new Array(longest + 2).join('`');
    }

    function languageOf(node) {
        var nodes = [node].concat(Array.prototype.slice.call(node.querySelectorAll('code')));
        for (var i = 0; i < nodes.length; i++) {
            var match = (nodes[i].className || '').match(/(?:^|\s)(?:language|lang)-([\w+#.-]{1,30})/);
            if (match) return match[1];
        }
        return '';
    }

    // Inline content: returns Markdown on one logical line (soft breaks kept as "  \n").
    function inline(node, ctx) {
        var out = '';
        node.childNodes.forEach(function (child) {
            out += inlineNode(child, ctx);
        });
        return out;
    }

    function wrapMarker(text, marker) {
        var match = text.match(/^(\s*)([\s\S]*?)(\s*)$/);
        if (!match[2]) return text;
        return match[1] + marker + match[2] + marker + match[3];
    }

    function inlineNode(node, ctx) {
        if (node.nodeType === 3) {
            var text = node.nodeValue.replace(/\s+/g, ' ');  // \s includes no-break spaces
            return ctx.code ? text : escapeText(text);
        }
        if (node.nodeType !== 1) return '';
        var t = tag(node);
        if (SKIP.test(t)) return '';
        // Word: the list bullet/number span, and its empty "o:p" paragraphs.
        if (/mso-list:\s*ignore/i.test(node.getAttribute('style') || '') || t === 'o:p') return '';
        if (t === 'br') return ctx.inTable ? ' ' : '  \n';
        if (t === 'img') {
            var src = safeUrl(node.getAttribute('src'));
            if (!src || /^data:/i.test(src)) return '';
            return '![' + escapeText((node.getAttribute('alt') || '').replace(/\s+/g, ' ').trim()) + '](' + src + ')';
        }
        if (BLOCK.test(t)) {
            // A block inside inline content (a <div> in a <span>, say).
            return ' ' + inline(node, ctx) + ' ';
        }
        if (isMonospace(node) && !ctx.code) {
            return codeSpan(node.textContent);
        }
        var content = inline(node, ctx);
        if (t === 'a') {
            var href = safeUrl(node.getAttribute('href'));
            var label = content.trim();
            if (!href || href.charAt(0) === '#' || !label) return content;
            if (label === escapeText(href)) return '<' + href + '>';
            var title = (node.getAttribute('title') || '').replace(/"/g, '\'').trim();
            return '[' + label + '](' + href + (title ? ' "' + title + '"' : '') + ')';
        }
        if (ctx.code) return content;
        var bold = isBold(node), italic = isItalic(node);
        if (bold && !ctx.bold) {
            content = wrapMarker(content, '**');
        }
        if (italic && !ctx.italic) {
            content = wrapMarker(content, '*');
        }
        return content;
    }

    function inlineWith(node, ctx, extra) {
        var sub = Object.assign({}, ctx, extra);
        return inline(node, sub);
    }

    function cleanInline(text) {
        return text.replace(/[ \t]+/g, ' ')
                   .replace(/ *  \n */g, '  \n')
                   .replace(/^\s+|\s+$/g, '')
                   .replace(/(  \n)+$/, '');
    }

    // Blocks: returns a list of Markdown blocks (strings), joined by blank lines.
    function blocks(node, ctx) {
        var out = [], line = '';
        function flush() {
            var text = escapeLineStarts(cleanInline(line));
            if (text) out.push(text);
            line = '';
        }
        node.childNodes.forEach(function (child) {
            var t = tag(child);
            if (child.nodeType === 1 && SKIP.test(t)) return;
            if (child.nodeType === 1 && (BLOCK.test(t) || isWordListItem(child))) {
                flush();
                out = out.concat(block(child, ctx));
            } else {
                var bold = child.nodeType === 1 && isBold(child);
                var italic = child.nodeType === 1 && isItalic(child);
                if (child.nodeType === 1 && child.querySelector && hasBlocks(child) && !bold && !italic) {
                    flush();
                    out = out.concat(blocks(child, ctx));
                } else {
                    line += inlineNode(child, ctx);
                }
            }
        });
        flush();
        return out;
    }

    function hasBlocks(node) {
        for (var i = 0; i < node.children.length; i++) {
            if (BLOCK.test(tag(node.children[i])) || isWordListItem(node.children[i])) return true;
        }
        return false;
    }

    function isWordListItem(node) {
        return tag(node) === 'p' && (/mso-list/i.test(node.getAttribute('style') || '') ||
                                     /MsoListParagraph/.test(node.className || ''));
    }

    function indent(text, prefix) {
        return text.split('\n').map(function (l, i) { return l ? prefix + l : l; }).join('\n');
    }

    // A list item; its later blocks are indented four spaces (nested lists right under the text).
    function listItem(content, marker) {
        var body = content.reduce(function (out, block, n) {
            if (!n) return block;
            return out + (/^(- |\d+\. )/.test(block) ? '\n' : '\n\n') + block;
        }, '');
        return marker + ' ' + indent(body, '    ').replace(/^ {4}/, '');
    }

    function list(node, ctx) {
        var ordered = tag(node) === 'ol';
        var start = parseInt(node.getAttribute('start'), 10);
        var n = isNaN(start) ? 1 : start;
        var items = [];
        node.childNodes.forEach(function (child) {
            if (child.nodeType === 3 && !child.nodeValue.trim()) return;
            if (tag(child) === 'ul' || tag(child) === 'ol') {
                // A list directly in a list (invalid but common): nest it in the last item.
                var nested = indent(list(child, ctx), '    ');
                if (items.length) items[items.length - 1] += '\n' + nested;
                else items.push(nested);
                return;
            }
            var content = tag(child) === 'li' ? blocks(child, ctx) : [escapeLineStarts(cleanInline(inlineNode(child, ctx)))];
            content = content.filter(Boolean);
            items.push(listItem(content, ordered ? (n++) + '.' : '-'));
        });
        // Loose items (several blocks) need blank lines between items.
        var loose = items.some(function (i) { return /\n\n/.test(i); });
        return items.join(loose ? '\n\n' : '\n');
    }

    function table(node, ctx) {
        var rows = Array.prototype.filter.call(node.querySelectorAll('tr'), function (tr) {
            return tr.closest('table') === node;
        });
        if (!rows.length) return [];
        var cells = rows.map(function (tr) {
            return Array.prototype.filter.call(tr.children, function (c) { return /^t[dh]$/.test(tag(c)); })
                .map(function (c) {
                    var text = cleanInline(inlineWith(c, ctx, {inTable: true})).replace(/\s*\n\s*/g, ' ');
                    return text.replace(/\|/g, '\\|');
                });
        }).filter(function (r) { return r.length; });
        if (!cells.length) return [];
        var width = Math.max.apply(null, cells.map(function (r) { return r.length; }));
        cells.forEach(function (r) { while (r.length < width) r.push(''); });
        var line = function (r) { return '| ' + r.join(' | ') + ' |'; };
        var head = cells[0];
        var sep = head.map(function () { return '---'; });
        return [[line(head), line(sep)].concat(cells.slice(1).map(line)).join('\n')];
    }

    function block(node, ctx) {
        var t = tag(node);
        if (isWordListItem(node)) {
            var bullet = node.querySelector('[style*="mso-list"]');
            var mark = bullet ? bullet.textContent.trim() : '';
            var level = parseInt(((node.getAttribute('style') || '').match(/level(\d+)/) || [])[1], 10) || 1;
            var text = escapeLineStarts(cleanInline(inline(node, ctx)));
            var item = (/^\(?\w{1,3}[.)]$/.test(mark) && /\d/.test(mark) ? '1.' : '-') + ' ' + text;
            return ['\u0000LI' + new Array(level).join('    ') + item];
        }
        if (/^h[1-6]$/.test(t)) {
            var heading = cleanInline(inlineWith(node, ctx, {bold: true})).replace(/\s*\n\s*/g, ' ');
            return heading ? [new Array(+t.charAt(1) + 1).join('#') + ' ' + heading] : [];
        }
        if (t === 'hr') return ['---'];
        if (t === 'pre') {
            var code = node.textContent.replace(/\r\n?/g, '\n').replace(/\n$/, '');
            var fence = fenceFor(code);
            return [fence + languageOf(node) + '\n' + code + '\n' + fence];
        }
        if (t === 'blockquote') {
            var inner = blocks(node, ctx).join('\n\n');
            return inner ? [inner.split('\n').map(function (l) { return l ? '> ' + l : '>'; }).join('\n')] : [];
        }
        if (t === 'ul' || t === 'ol') {
            var md = list(node, ctx);
            return md ? [md] : [];
        }
        if (t === 'table') return table(node, ctx);
        if (t === 'li') {
            return [listItem(blocks(node, ctx), '-')];
        }
        if (t === 'p' && /white-space:\s*pre/.test(node.getAttribute('style') || '')) {
            return [node.textContent];
        }
        return blocks(node, ctx);
    }

    // Word list paragraphs come one by one: join neighbours into one list.
    function joinWordLists(list) {
        var out = [];
        list.forEach(function (b) {
            if (b.indexOf('\u0000LI') === 0) {
                var item = b.slice(3);
                if (out.length && out[out.length - 1].list) {
                    out[out.length - 1].text += '\n' + item;
                } else {
                    out.push({list: true, text: item});
                }
            } else {
                out.push({text: b});
            }
        });
        return out.map(function (o) { return o.text; });
    }

    function htmlToMarkdown(html) {
        var doc = parse(html);
        var body = doc.body;
        if (!body) return '';
        // Word's conditional comments and the clipboard's fragment markers.
        var walker = doc.createTreeWalker(body, NodeFilter.SHOW_COMMENT);
        var comments = [];
        while (walker.nextNode()) comments.push(walker.currentNode);
        comments.forEach(function (c) { c.remove(); });
        var md = joinWordLists(blocks(body, {})).join('\n\n');
        return md.replace(/\n{3,}/g, '\n\n').replace(/[ \t]+$/gm, function (s) {
            return s === '  ' ? s : '';
        }).trim();
    }

    function htmlIsRich(html) {
        if (!html || !/<[a-z]/i.test(html)) return false;
        var body = parse(html).body;
        return !!(body && body.querySelector(RICH));
    }

    window.osqaHtmlToMarkdown = htmlToMarkdown;
    window.osqaHtmlIsRich = htmlIsRich;
})();
