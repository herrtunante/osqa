/*
 * Question page: a "Copy" button on every code block in a post, and the
 * Space key for links that act as buttons (votes, favourite, accept).
 * Texts come from `messages` (set in question.html, translated).
 */
(function() {
    var SELECTOR = '.question-body pre, .answer-body pre, .ai-suggestion-body pre';

    function text(name, fallback) {
        return (window.messages && window.messages[name]) || fallback;
    }

    // One live region for the whole page announces "Copied".
    function announce(message) {
        var region = document.getElementById('copy-status');
        if (!region) {
            region = document.createElement('div');
            region.id = 'copy-status';
            region.className = 'visually-hidden';
            region.setAttribute('role', 'status');
            region.setAttribute('aria-live', 'polite');
            document.body.appendChild(region);
        }
        region.textContent = '';
        setTimeout(function() { region.textContent = message; }, 50);
    }

    function copyText(value) {
        if (navigator.clipboard && window.isSecureContext) {
            return navigator.clipboard.writeText(value);
        }
        return new Promise(function(resolve, reject) {
            var area = document.createElement('textarea');
            area.value = value;
            area.setAttribute('readonly', '');
            area.style.position = 'fixed';
            area.style.opacity = '0';
            document.body.appendChild(area);
            area.select();
            var ok = false;
            try { ok = document.execCommand('copy'); } catch (e) {}
            document.body.removeChild(area);
            if (ok) { resolve(); } else { reject(); }
        });
    }

    function addCopyButton(pre) {
        if (pre.parentNode.classList.contains('code-block')) {
            return;
        }
        var wrapper = document.createElement('div');
        wrapper.className = 'code-block';
        pre.parentNode.insertBefore(wrapper, pre);
        wrapper.appendChild(pre);

        var button = document.createElement('button');
        button.type = 'button';
        button.className = 'code-copy plain-button';
        button.textContent = text('copy_code', 'Copy');
        button.setAttribute('aria-label', text('copy_code_label', 'Copy code to the clipboard'));
        wrapper.appendChild(button);

        var timer = null;
        button.addEventListener('click', function() {
            var code = pre.querySelector('code') || pre;
            copyText(code.textContent.replace(/\n$/, '')).then(function() {
                button.textContent = text('copied', 'Copied');
                button.classList.add('copied');
                announce(text('copied', 'Copied'));
            }, function() {
                announce(text('copy_failed', 'Press Ctrl+C to copy'));
                var range = document.createRange();
                range.selectNodeContents(code);
                var selection = window.getSelection();
                selection.removeAllRanges();
                selection.addRange(range);
            });
            clearTimeout(timer);
            timer = setTimeout(function() {
                button.textContent = text('copy_code', 'Copy');
                button.classList.remove('copied');
            }, 2000);
        });
    }

    function init() {
        var blocks = document.querySelectorAll(SELECTOR);
        for (var i = 0; i < blocks.length; i++) {
            addCopyButton(blocks[i]);
        }
    }

    // Links with role="button" (vote, favourite, accept) answer to Space like buttons.
    document.addEventListener('keydown', function(evt) {
        var el = evt.target;
        if ((evt.key === ' ' || evt.key === 'Spacebar') && el.tagName === 'A' && el.getAttribute('role') === 'button') {
            evt.preventDefault();
            el.click();
        }
    });

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
    window.osqaCopyButtons = init;
})();
