/*
 * Tags as chips (ask, edit and retag pages, templates/compose/tags.html).
 *
 * The server's text box (#id_tags) stays where it is and becomes the box
 * people type into; its name moves to a hidden input (#id_tags_value) that
 * always holds the space-separated tags the server expects. Enter, comma or
 * space turns the typed word into a chip; Backspace in the empty box removes
 * the last chip; suggestions come from the matching_tags view through
 * $.fn.osqaAutocomplete. Changes are announced in a polite live region.
 * Without JavaScript the plain text box is sent as before.
 */
(function ($) {
    function format(template, value) {
        return (template || '%s').replace('%s', value);
    }

    function setup(box) {
        var entry = box.querySelector('input[type=text], input:not([type])');
        if (!entry || !entry.name || box.classList.contains('is-ready')) return;
        var form = entry.form;
        var tags = [];
        var syncing = false;

        var hidden = document.createElement('input');
        hidden.type = 'hidden';
        hidden.name = entry.name;
        hidden.id = entry.id + '_value';
        entry.removeAttribute('name');
        entry.removeAttribute('required');
        box.parentNode.insertBefore(hidden, box.nextSibling);

        var list = document.createElement('ul');
        list.className = 'tag-chips-list';
        list.setAttribute('aria-label', box.getAttribute('data-list') || 'Tags');
        box.insertBefore(list, entry);

        var status = document.createElement('span');
        status.className = 'visually-hidden';
        status.setAttribute('role', 'status');
        status.setAttribute('aria-live', 'polite');
        box.parentNode.insertBefore(status, hidden.nextSibling);

        entry.classList.add('tag-chips-entry');
        entry.setAttribute('autocomplete', 'off');
        entry.setAttribute('autocapitalize', 'none');
        entry.setAttribute('spellcheck', 'false');
        entry.setAttribute('enterkeyhint', 'done');
        box.classList.add('is-ready');

        function split(text) {
            return text.toLowerCase().split(/[\s,]+/).filter(function (t) { return t; });
        }

        function announce(text) {
            status.textContent = '';
            setTimeout(function () { status.textContent = text; }, 30);
        }

        function render() {
            list.innerHTML = '';
            tags.forEach(function (tag) {
                var li = document.createElement('li');
                li.className = 'tag-chip';
                var name = document.createElement('span');
                name.className = 'tag-chip-name';
                name.textContent = tag;
                var remove = document.createElement('button');
                remove.type = 'button';
                remove.className = 'tag-chip-remove plain-button';
                remove.setAttribute('aria-label', format(box.getAttribute('data-remove'), tag));
                remove.setAttribute('data-tag', tag);
                remove.innerHTML = '<svg viewBox="0 0 16 16" width="12" height="12" aria-hidden="true" focusable="false">' +
                    '<path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="2" stroke-linecap="round" fill="none"/></svg>';
                li.appendChild(name);
                li.appendChild(remove);
                list.appendChild(li);
            });
            list.hidden = !tags.length;
            entry.setAttribute('placeholder', '');
            hidden.value = tags.join(' ');
            // Lets the draft keeper (osqa.drafts.js) save the tags.
            syncing = true;
            hidden.dispatchEvent(new Event('input', {bubbles: true}));
            syncing = false;
        }

        function add(text, quiet) {
            var added = [];
            split(text).forEach(function (tag) {
                if (tags.indexOf(tag) < 0) {
                    tags.push(tag);
                    added.push(tag);
                }
            });
            if (added.length) {
                render();
                if (!quiet) announce(added.map(function (t) { return format(box.getAttribute('data-added'), t); }).join('. '));
            }
            return added.length;
        }

        function remove(tag) {
            var index = tags.indexOf(tag);
            if (index < 0) return;
            tags.splice(index, 1);
            render();
            announce(format(box.getAttribute('data-removed'), tag));
        }

        // Typed text not yet turned into a chip.
        function commit() {
            if (entry.value.trim()) add(entry.value);
            entry.value = '';
        }

        function menuOpen() {
            var widget = $(entry).data('ui-autocomplete');
            return widget && widget.menu && widget.menu.element.is(':visible');
        }

        function closeMenu() {
            if ($(entry).data('ui-autocomplete')) $(entry).autocomplete('close');
        }

        // The server's value (or the one sent back with errors).
        add(entry.value, true);
        entry.value = '';
        render();

        if ($.fn.osqaAutocomplete && box.getAttribute('data-url')) {
            $(entry).osqaAutocomplete(box.getAttribute('data-url'), {
                max: 20,
                select: function (row) {
                    entry.value = '';
                    add(row[1]);
                    setTimeout(function () { entry.value = ''; entry.focus(); }, 0);
                }
            });
        }

        entry.addEventListener('keydown', function (e) {
            if (e.isComposing || e.ctrlKey || e.metaKey || e.altKey) return;
            if (e.key === 'Enter') {
                if (menuOpen() && $(entry).data('ui-autocomplete').menu.active) return; // the menu picks it
                if (entry.value.trim()) {
                    e.preventDefault();
                    closeMenu();
                    commit();
                } else if (form && form.id !== 'fmretag') {
                    e.preventDefault(); // no accidental posting from the tags box
                }
            } else if (e.key === ',' || e.key === ' ') {
                e.preventDefault();
                closeMenu();
                commit();
            } else if (e.key === 'Backspace' && !entry.value && entry.selectionStart === 0 && tags.length) {
                e.preventDefault();
                remove(tags[tags.length - 1]);
            }
        });

        // Pasted (or filled in) text with separators: everything before the last word becomes chips.
        entry.addEventListener('input', function () {
            var value = entry.value;
            if (!/[\s,]/.test(value)) return;
            var parts = value.split(/[\s,]+/);
            var rest = /[\s,]$/.test(value) ? '' : parts.pop();
            add(parts.join(' '));
            entry.value = rest;
        });

        entry.addEventListener('blur', function () {
            setTimeout(function () {
                if (document.activeElement !== entry && !menuOpen()) commit();
            }, 200);
        });

        list.addEventListener('click', function (e) {
            var button = e.target.closest('.tag-chip-remove');
            if (!button) return;
            remove(button.getAttribute('data-tag'));
            entry.focus();
        });

        box.addEventListener('click', function (e) {
            if (e.target === box || e.target === list) entry.focus();
        });

        // A restored draft sets the hidden value.
        hidden.addEventListener('input', function () {
            if (syncing) return;
            tags = [];
            add(hidden.value, true);
            render();
        });

        if (form) {
            form.addEventListener('submit', commit);
            form.addEventListener('compose:beforesubmit', commit);
        }
    }

    document.querySelectorAll('.tag-chips').forEach(setup);
})(jQuery);
