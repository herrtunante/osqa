/*
 * Keeps drafts of a new question and of answers in the browser's
 * localStorage, so a closed tab or a crash does not lose the text.
 * Drafts are restored on the next visit and removed when the post is sent.
 */
(function() {
    var PREFIX = 'osqa-draft:';
    var MAX_AGE = 14 * 24 * 3600 * 1000;

    function storage() {
        try {
            var s = window.localStorage;
            s.setItem(PREFIX + 'test', '1');
            s.removeItem(PREFIX + 'test');
            return s;
        } catch (e) {
            return null;
        }
    }

    function setup(form, key, fieldIds) {
        var store = storage();
        if (!store || !form) {
            return;
        }
        var fields = fieldIds.map(function(id) { return document.getElementById(id); })
                             .filter(function(f) { return f; });
        if (!fields.length) {
            return;
        }

        var saved = null;
        try {
            saved = JSON.parse(store.getItem(key) || 'null');
        } catch (e) {}

        var empty = fields.every(function(f) { return !f.value.trim(); });
        if (saved && saved.fields && Date.now() - saved.time < MAX_AGE && empty) {
            fields.forEach(function(f) {
                if (saved.fields[f.id]) {
                    f.value = saved.fields[f.id];
                    // Let the editor refresh its preview.
                    f.dispatchEvent(new Event('input', {bubbles: true}));
                    f.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
                }
            });
            showNotice(form, function() {
                store.removeItem(key);
                fields.forEach(function(f) {
                    f.value = '';
                    f.dispatchEvent(new Event('input', {bubbles: true}));
                    f.dispatchEvent(new KeyboardEvent('keyup', {bubbles: true}));
                });
            });
        } else if (saved && Date.now() - saved.time >= MAX_AGE) {
            store.removeItem(key);
        }

        var timer = null;
        function save() {
            clearTimeout(timer);
            timer = setTimeout(function() {
                var data = {time: Date.now(), fields: {}};
                var any = false;
                fields.forEach(function(f) {
                    data.fields[f.id] = f.value;
                    any = any || !!f.value.trim();
                });
                try {
                    if (any) {
                        store.setItem(key, JSON.stringify(data));
                    } else {
                        store.removeItem(key);
                    }
                } catch (e) {}
            }, 600);
        }
        fields.forEach(function(f) {
            f.addEventListener('input', save);
        });

        // The forms are sent with form.submit() from the button's onclick,
        // which does not fire a submit event.
        function clear() {
            clearTimeout(timer);
            store.removeItem(key);
        }
        form.addEventListener('submit', clear);
        var buttons = form.querySelectorAll('input.submit, input[type=submit], button[type=submit]');
        for (var i = 0; i < buttons.length; i++) {
            buttons[i].addEventListener('click', clear, true);
        }
    }

    function showNotice(form, onDiscard) {
        var notice = document.createElement('div');
        notice.className = 'draft-notice';
        notice.setAttribute('role', 'status');
        var text = document.createElement('span');
        text.className = 'draft-notice-text';
        text.textContent = (window.messages && messages.draft_restored) || 'Draft restored.';
        notice.appendChild(text);
        var discard = document.createElement('button');
        discard.type = 'button';
        discard.className = 'draft-discard plain-button';
        discard.textContent = (window.messages && messages.discard) || 'Discard';
        discard.addEventListener('click', function() {
            onDiscard();
            notice.remove();
        });
        notice.appendChild(discard);
        form.insertBefore(notice, form.firstChild);
    }

    document.addEventListener('DOMContentLoaded', function() {
        // With the tag chips (osqa.tagchips.js) the tags are in a hidden input.
        var tags = document.getElementById('id_tags_value') ? 'id_tags_value' : 'id_tags';
        setup(document.getElementById('fmask'), PREFIX + 'ask', ['id_title', 'editor', tags]);

        var answer = document.getElementById('fmanswer');
        if (answer) {
            var match = answer.getAttribute('action').match(/\/(\d+)\//);
            if (match) {
                setup(answer, PREFIX + 'answer:' + match[1], ['editor']);
            }
        }
    });
})();
