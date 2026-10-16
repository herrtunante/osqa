/*
 * @username autocomplete in comment boxes and the post editor: after "@" and
 * a letter, up to six members (matching_users); Up/Down choose, Enter or Tab
 * insert "@username ", Escape closes. The text box points at the list
 * (aria-controls, aria-activedescendant); in the post editor the insertion
 * is one undo step (osqa.editor.js). A polite status says how many were found.
 */
(function() {
    var SELECTOR = 'textarea.commentBox, textarea#editor';
    var TRIGGER = /(^|[^\w@\/.])@([\w-]{1,30})$/;
    var box = null, items = [], active = 0, target = null, start = 0, timer = null, cache = {};

    function caretPosition(textarea) {
        // Mirror the textarea in a hidden div to find the caret's coordinates.
        var mirror = document.createElement('div');
        var style = window.getComputedStyle(textarea);
        ['boxSizing', 'width', 'fontFamily', 'fontSize', 'fontWeight', 'lineHeight', 'letterSpacing',
         'paddingTop', 'paddingRight', 'paddingBottom', 'paddingLeft', 'borderTopWidth', 'borderLeftWidth',
         'textIndent', 'whiteSpace', 'wordWrap', 'tabSize'].forEach(function(p) { mirror.style[p] = style[p]; });
        mirror.style.position = 'absolute';
        mirror.style.visibility = 'hidden';
        mirror.style.whiteSpace = 'pre-wrap';
        mirror.style.wordWrap = 'break-word';
        mirror.textContent = textarea.value.slice(0, textarea.selectionStart);
        var marker = document.createElement('span');
        marker.textContent = '​';
        mirror.appendChild(marker);
        document.body.appendChild(mirror);
        var rect = textarea.getBoundingClientRect();
        var pos = {
            left: rect.left + window.scrollX + marker.offsetLeft - textarea.scrollLeft,
            top: rect.top + window.scrollY + marker.offsetTop - textarea.scrollTop + parseFloat(style.lineHeight || 18)
        };
        document.body.removeChild(mirror);
        return pos;
    }

    function removeBox() {
        if (box) {
            box.remove();
        }
        box = null;
        if (target) {
            target.removeAttribute('aria-activedescendant');
            target.removeAttribute('aria-controls');
        }
    }

    function close() {
        removeBox();
        items = [];
    }

    // How many members were found, for screen readers (the list itself is
    // only pointed at by aria-activedescendant).
    var status = null;
    function announce() {
        if (!status) {
            status = document.createElement('div');
            status.className = 'visually-hidden';
            status.setAttribute('role', 'status');
            document.body.appendChild(status);
        }
        var m = window.messages || {};
        status.textContent = !items.length ? '' : items.length === 1 ? (m.suggestion_one || '1 suggestion') :
            (m.suggestion_many || '{count} suggestions').replace('{count}', items.length);
    }

    function render() {
        removeBox();
        if (!items.length) {
            return;
        }
        box = document.createElement('ul');
        box.className = 'mention-suggestions';
        box.id = 'mention-suggestions';
        box.setAttribute('role', 'listbox');
        box.setAttribute('aria-label', (window.messages && messages.mention_suggestions) || 'Members');
        items.forEach(function(item, i) {
            var li = document.createElement('li');
            li.setAttribute('role', 'option');
            li.id = 'mention-option-' + i;
            li.setAttribute('aria-selected', i === active ? 'true' : 'false');
            if (i === active) {
                li.className = 'active';
            }
            var name = document.createElement('span');
            name.textContent = item.name;
            var rep = document.createElement('span');
            rep.className = 'mention-rep';
            rep.textContent = item.rep;
            li.appendChild(name);
            li.appendChild(rep);
            li.addEventListener('mousedown', function(evt) {
                evt.preventDefault();
                choose(i);
            });
            box.appendChild(li);
        });
        var pos = caretPosition(target);
        var right = window.scrollX + document.documentElement.clientWidth - 8;
        document.body.appendChild(box);
        box.style.left = Math.max(window.scrollX + 8, Math.min(pos.left, right - box.offsetWidth)) + 'px';
        box.style.top = pos.top + 'px';
        target.setAttribute('aria-autocomplete', 'list');
        target.setAttribute('aria-controls', box.id);
        target.setAttribute('aria-activedescendant', 'mention-option-' + active);
    }

    function choose(i) {
        var item = items[i];
        if (!item || !target) {
            return;
        }
        var value = target.value, caret = target.selectionStart;
        var insert = '@' + item.username + ' ';
        var newValue = value.slice(0, start) + insert + value.slice(caret);
        var newCaret = start + insert.length;
        if (target.osqaEditor) {
            target.osqaEditor.apply(newValue, newCaret);
        } else {
            target.value = newValue;
            target.setSelectionRange(newCaret, newCaret);
            target.dispatchEvent(new Event('input', {bubbles: true}));
            target.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
        }
        close();
        target.focus();
    }

    function lookup(query) {
        if (cache[query]) {
            items = cache[query];
            active = 0;
            render();
            announce();
            return;
        }
        fetch(messages.matching_users_url + '?limit=6&q=' + encodeURIComponent(query), {credentials: 'same-origin'})
            .then(function(r) { return r.ok ? r.text() : ''; })
            .then(function(body) {
                var found = body.split('\n').filter(Boolean).map(function(line) {
                    var f = line.split('|');
                    return {name: f[1], rep: f[2], username: f[3] || f[1]};
                });
                cache[query] = found;
                if (target && document.activeElement === target) {
                    items = found;
                    active = 0;
                    render();
                    announce();
                }
            }).catch(function(err) {
                if (window.console) {
                    console.error('mention lookup failed', err);
                }
                close();
            });
    }

    document.addEventListener('input', function(evt) {
        var el = evt.target;
        if (!el.matches || !el.matches(SELECTOR) || !evt.isTrusted) {
            return;
        }
        var before = el.value.slice(0, el.selectionStart);
        var match = before.match(TRIGGER);
        clearTimeout(timer);
        if (!match) {
            close();
            return;
        }
        target = el;
        start = el.selectionStart - match[2].length - 1;
        timer = setTimeout(function() { lookup(match[2]); }, 150);
    });

    document.addEventListener('keydown', function(evt) {
        if (!box || evt.target !== target) {
            return;
        }
        if (evt.key === 'ArrowDown' || evt.key === 'ArrowUp') {
            active = (active + (evt.key === 'ArrowDown' ? 1 : items.length - 1)) % items.length;
            render();
        } else if ((evt.key === 'Enter' || evt.key === 'Tab') && !evt.ctrlKey && !evt.metaKey) {
            choose(active);
        } else if (evt.key === 'Escape') {
            close();
        } else {
            return;
        }
        evt.preventDefault();
        evt.stopImmediatePropagation();
    }, true);

    document.addEventListener('focusout', function(evt) {
        if (evt.target === target) {
            setTimeout(close, 100);
        }
    });
})();
