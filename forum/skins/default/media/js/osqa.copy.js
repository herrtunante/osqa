/* Copy buttons: <button data-copy-target="#field" data-copied-text="Copied">
   copies the value (or text) of the target to the clipboard, says so in the
   button and in the nearest [role=status], and selects the text when the
   clipboard can't be used (plain http), so it can be copied by hand.
   Used by the API tokens page (users/api_tokens.html). */
(function () {
    'use strict';

    function announce(button, text) {
        var section = button.closest('section') || document;
        var status = section.querySelector('[role="status"]');
        if (status) status.textContent = text;
    }

    document.addEventListener('click', function (evt) {
        var button = evt.target.closest ? evt.target.closest('[data-copy-target]') : null;
        if (!button) return;
        var target = document.querySelector(button.getAttribute('data-copy-target'));
        if (!target) return;
        var text = 'value' in target ? target.value : target.textContent;
        var done = function () {
            var copied = button.getAttribute('data-copied-text') || 'Copied';
            if (!button.hasAttribute('data-label')) button.setAttribute('data-label', button.textContent);
            button.textContent = copied;
            announce(button, copied);
            window.setTimeout(function () { button.textContent = button.getAttribute('data-label'); }, 2500);
        };
        var byHand = function () {
            if (target.select) target.select();
            try {
                if (document.execCommand('copy')) done();
            } catch (e) { /* the text stays selected */ }
        };
        if (navigator.clipboard && window.isSecureContext) {
            navigator.clipboard.writeText(text).then(done, byHand);
        } else {
            byHand();
        }
    });
})();
