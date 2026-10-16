/*
 * Keyboard shortcuts on the public pages; "?" lists them in a dialog
 * (display/shortcuts_dialog.html, which also holds the URLs).
 *
 *   ?            the list (a modal <dialog>: the focus stays inside, Escape closes)
 *   /            the header search box (osqa.searchbox.js)
 *   g h/q/t/u/n  home, questions, tags, users, notifications (members)
 *   a            ask a question
 *   j / k        next / previous question in a list, or post on a question page
 *   Enter        opens the question that has the focus
 *   u / d        vote the focused post up / down (members; the server's rules apply)
 *
 * Never while typing (text fields, selects, contenteditable), with Ctrl, Alt
 * or Meta, or while another dialog is open. Members turn them off in their
 * preferences: <html data-shortcuts="off"> and no dialog on the page. The
 * moderation inbox (admin) has its own j / k (osqa.moderation.js).
 */
(function () {
    'use strict';

    var dialog = document.getElementById('shortcuts-dialog');
    if (!dialog || document.documentElement.getAttribute('data-shortcuts') === 'off') {
        return;
    }

    var GO = {h: 'home', q: 'questions', t: 'tags', u: 'users', n: 'notifications'};
    var pendingGo = null;
    var opener = null;

    function typing(target) {
        return target && target.closest &&
            !!target.closest('input, textarea, select, [contenteditable]:not([contenteditable="false"])');
    }

    // Native dialogs, the post commands' dialogs (osqa.main.js) and jQuery UI's.
    function otherDialogOpen() {
        return Array.prototype.some.call(document.querySelectorAll('dialog[open], div.dialog, .ui-dialog'), function (el) {
            return el !== dialog && el.offsetParent !== null;
        }) || !!document.querySelector('dialog[open]:not(#shortcuts-dialog)');
    }

    function go(name) {
        var url = dialog.getAttribute('data-url-' + name);
        if (url) {
            window.location.assign(url);
        }
    }

    // -- the dialog -------------------------------------------------------

    function focusables() {
        return Array.prototype.filter.call(dialog.querySelectorAll('a[href], button, [tabindex]:not([tabindex="-1"])'),
            function (el) { return !el.disabled && el.offsetParent !== null; });
    }

    function openDialog() {
        if (dialog.open || typeof dialog.showModal !== 'function') {
            return;
        }
        opener = document.activeElement;
        dialog.showModal();
        var first = focusables()[0];
        if (first) {
            first.focus();
        }
    }

    dialog.addEventListener('close', function () {
        if (opener && opener.focus && document.contains(opener)) {
            opener.focus();
        }
        opener = null;
    });
    dialog.addEventListener('click', function (event) {
        // The close button, or a click on the backdrop (outside the box).
        if (event.target.closest('[data-shortcuts-close]') || event.target === dialog) {
            dialog.close();
        }
    });
    dialog.addEventListener('keydown', function (event) {
        if (event.key !== 'Tab') {
            return;
        }
        var items = focusables();
        if (!items.length) {
            event.preventDefault();
            return;
        }
        var first = items[0], last = items[items.length - 1];
        if (event.shiftKey && document.activeElement === first) {
            event.preventDefault();
            last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
            event.preventDefault();
            first.focus();
        }
    });

    // -- moving through questions and posts ------------------------------

    function items() {
        var selector = document.querySelector('.question-page') ? '.question-page .post' : '.question-row';
        return Array.prototype.filter.call(document.querySelectorAll(selector), function (el) {
            return el.offsetParent !== null;
        });
    }

    function current() {
        return document.querySelector('.is-kbd-current');
    }

    function move(step) {
        var list = items();
        if (!list.length) {
            return false;
        }
        var index = list.indexOf(current());
        index = index < 0 ? 0 : Math.max(0, Math.min(list.length - 1, index + step));
        var item = list[index];
        list.forEach(function (el) { el.classList.remove('is-kbd-current'); });
        item.classList.add('is-kbd-current');
        if (!item.hasAttribute('tabindex')) {
            item.setAttribute('tabindex', '-1');
        }
        item.focus({preventScroll: true});
        item.scrollIntoView({block: 'nearest'});
        return true;
    }

    function openCurrent(target) {
        var item = current();
        if (!item || target !== item || !item.classList.contains('question-row')) {
            return false;
        }
        var link = item.querySelector('h2 a[href]');
        if (link) {
            window.location.assign(link.href);
        }
        return !!link;
    }

    function vote(direction) {
        var item = current();
        if (!item || !dialog.hasAttribute('data-signed-in')) {
            return false;
        }
        var button = item.querySelector('.post-votes a.post-vote.' + direction);
        if (!button) {
            return false;
        }
        button.click();
        return true;
    }

    // The focus left the item (a click, Tab): it is no longer the current one.
    document.addEventListener('focusin', function (event) {
        var item = current();
        if (item && !item.contains(event.target)) {
            item.classList.remove('is-kbd-current');
        }
    });

    // -- keys --------------------------------------------------------------

    document.addEventListener('keydown', function (event) {
        if (event.defaultPrevented || event.ctrlKey || event.metaKey || event.altKey || event.isComposing ||
                dialog.open || typing(event.target) || otherDialogOpen()) {
            return;
        }
        var key = event.key, handled = false;
        if (pendingGo) {
            clearTimeout(pendingGo);
            pendingGo = null;
            if (GO[key]) {
                go(GO[key]);
                handled = true;
            }
        } else if (key === '?') {
            openDialog();
            handled = true;
        } else if (key === 'g') {
            pendingGo = setTimeout(function () { pendingGo = null; }, 1500);
            handled = true;
        } else if (key === 'a') {
            go('ask');
            handled = true;
        } else if (key === 'j' || key === 'k') {
            handled = move(key === 'j' ? 1 : -1);
        } else if (key === 'Enter') {
            handled = openCurrent(event.target);
        } else if (key === 'u' || key === 'd') {
            handled = vote(key === 'u' ? 'up' : 'down');
        }
        if (handled) {
            event.preventDefault();
        }
    });
})();
