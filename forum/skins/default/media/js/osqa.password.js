/*
 * Password fields (templates/auth/field.html, auth/password_toggle.html).
 *
 * Show-password buttons: pressing one shows the password typed in the field it
 * controls; pressing it again hides it. The buttons are hidden until this
 * script runs.
 *
 * Strength meter (inputs with data-password-meter): as the person types, the
 * rules listed under the field (data-rule="length" / "similar") are ticked with
 * the same limits the server checks (forum/utils/passwords.py, Django's
 * similarity check), and a bar shows a rough strength. The server still decides:
 * common and breached passwords can only be checked there. Pasting and password
 * managers are never blocked.
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

    // -- strength meter ------------------------------------------------------

    function userWords(input) {
        var words = (input.getAttribute('data-user-words') || '').split(/\s+/);
        (input.getAttribute('data-user-inputs') || '').split(/\s+/).forEach(function (id) {
            var other = id && document.getElementById(id);
            if (other && other.value) {
                words.push(other.value.split('@')[0]);
            }
        });
        return words.map(function (w) { return w.toLowerCase(); }).filter(function (w) { return w.length >= 3; });
    }

    // Like Django's UserAttributeSimilarityValidator: too close when one
    // contains the other, or most of the password is the word.
    function tooSimilar(password, words) {
        var lower = password.toLowerCase();
        return words.some(function (word) {
            return lower.indexOf(word) !== -1 && word.length * 10 >= lower.length * 7 || word.indexOf(lower) !== -1;
        });
    }

    function strength(password) {
        var length = Array.from(password).length;
        var kinds = [/[a-z]/, /[A-Z]/, /[0-9]/, /[^A-Za-z0-9]/].filter(function (re) { return re.test(password); }).length;
        var distinct = new Set(Array.from(password.toLowerCase())).size;
        var score = length >= 20 ? 4 : length >= 15 ? 3 : length >= 12 ? 2 : 1;
        if (kinds >= 3 && length >= 10) {
            score += 1;
        }
        if (distinct < 5) {
            score = 1;
        }
        return Math.min(score, 4);
    }

    function update(input) {
        var meter = document.querySelector('.password-meter[data-meter-for="' + input.id + '"]');
        if (!meter) {
            return;
        }
        var field = input.closest('.auth-field');
        var password = input.value;
        var minLength = parseInt(input.getAttribute('data-min-length'), 10) || 8;
        var longEnough = Array.from(password).length >= minLength;
        var similar = password.length > 0 && tooSimilar(password, userWords(input));
        if (field) {
            field.querySelectorAll('[data-rule="length"]').forEach(function (rule) {
                rule.classList.toggle('is-met', longEnough);
            });
            field.querySelectorAll('[data-rule="similar"]').forEach(function (rule) {
                rule.classList.toggle('is-met', password.length > 0 && !similar);
                rule.classList.toggle('is-unmet', similar);
            });
        }
        meter.hidden = password.length === 0;
        var level, key;
        if (!longEnough) {
            level = 0; key = 'short';
        } else if (similar) {
            level = 0; key = 'similar';
        } else {
            level = strength(password);
            key = ['short', 'weak', 'fair', 'good', 'strong'][level];
            if (level === 1) {
                key = 'weak';
            }
        }
        meter.setAttribute('data-level', String(level));
        meter.querySelector('.password-meter-label').textContent = meter.getAttribute('data-text-' + key) || '';
    }

    document.addEventListener('input', function (event) {
        var target = event.target;
        if (target.matches && target.matches('[data-password-meter]')) {
            update(target);
        } else if (target.id) {
            document.querySelectorAll('[data-password-meter]').forEach(function (input) {
                if ((input.getAttribute('data-user-inputs') || '').split(/\s+/).indexOf(target.id) !== -1 && input.value) {
                    update(input);
                }
            });
        }
    });

    function init() {
        document.querySelectorAll('.password-toggle[hidden]').forEach(function (button) {
            button.hidden = false;
        });
        // A form shown again after an error, or filled in by a password manager.
        document.querySelectorAll('[data-password-meter]').forEach(function (input) {
            if (input.value) {
                update(input);
            }
        });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
