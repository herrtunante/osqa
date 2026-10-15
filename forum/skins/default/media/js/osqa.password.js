/*
 * Show-password buttons (templates/auth/password_toggle.html): pressing one
 * shows the password typed in the field it controls; pressing it again hides
 * it. The buttons are hidden until this script runs.
 */
(function () {
    'use strict';

    function setVisible(button, visible) {
        var input = document.getElementById(button.getAttribute('aria-controls'));
        if (!input) {
            return;
        }
        input.type = visible ? 'text' : 'password';
        button.setAttribute('aria-pressed', visible ? 'true' : 'false');
    }

    document.addEventListener('click', function (event) {
        var button = event.target.closest ? event.target.closest('.password-toggle') : null;
        if (button) {
            setVisible(button, button.getAttribute('aria-pressed') !== 'true');
        }
    });

    // Hidden again when the form is sent, so the browser offers to save it as a password.
    document.addEventListener('submit', function (event) {
        event.target.querySelectorAll('.password-toggle[aria-pressed="true"]').forEach(function (button) {
            setVisible(button, false);
        });
    });

    function init() {
        document.querySelectorAll('.password-toggle[hidden]').forEach(function (button) {
            button.hidden = false;
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
