/*
 * The theme switch: Light / Dark / System, in the header menu (members,
 * display/theme_menu.html) and in the footer (display/theme_switch.html).
 *
 * A choice applies at once through window.osqaTheme.apply() (the inline
 * script of {% theme_head %}, forum/templatetags/display_tags.py, which also
 * announces it with an "osqa:themechange" event), is kept in the osqa_theme
 * cookie right away, and is sent to set_theme so a member's choice follows
 * them to their other devices. Every switch on the page shows the current
 * choice. Without JavaScript the forms submit and come back to the page.
 */
(function () {
    'use strict';

    if (!window.osqaTheme) {
        return;  // the site's dark mode is off
    }

    function writeCookie(choice) {
        document.cookie = 'osqa_theme=' + choice + '; path=/; max-age=31536000; samesite=lax' +
            (location.protocol === 'https:' ? '; secure' : '');
    }

    function sync() {
        var choice = window.osqaTheme.choice();
        document.querySelectorAll('[data-theme-switch] [name="theme"]').forEach(function (field) {
            if (field.type === 'radio') {
                field.checked = field.value === choice;
            } else {
                field.value = choice;
            }
        });
    }

    function save(form, choice) {
        var data = new FormData(form);
        data.set('theme', choice);
        var token = document.querySelector('meta[name="csrf-token"]');
        fetch(form.action, {
            method: 'POST', body: data, credentials: 'same-origin',
            headers: {'Accept': 'application/json', 'X-CSRFToken': token ? token.content : ''}
        }).catch(function () {
            // Offline: the cookie keeps the choice on this device.
        });
    }

    document.addEventListener('change', function (event) {
        var field = event.target;
        var form = field.closest && field.closest('[data-theme-switch]');
        if (!form || field.name !== 'theme') {
            return;
        }
        var choice = field.value;
        window.osqaTheme.apply(choice);
        writeCookie(choice);
        sync();
        save(form, choice);
    });

    document.addEventListener('submit', function (event) {
        if (event.target.matches && event.target.matches('[data-theme-switch]')) {
            event.preventDefault();  // already applied and saved on change
        }
    });

    // The header menu closes with Escape (focus back on its button) and when
    // the focus or a click goes elsewhere.
    document.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape') {
            return;
        }
        var menu = event.target.closest && event.target.closest('[data-theme-menu][open]');
        if (menu) {
            menu.open = false;
            menu.querySelector('summary').focus();
        }
    });
    document.addEventListener('click', function (event) {
        document.querySelectorAll('[data-theme-menu][open]').forEach(function (menu) {
            if (!menu.contains(event.target)) {
                menu.open = false;
            }
        });
    });
    document.addEventListener('focusin', function (event) {
        document.querySelectorAll('[data-theme-menu][open]').forEach(function (menu) {
            if (!menu.contains(event.target)) {
                menu.open = false;
            }
        });
    });

    document.addEventListener('osqa:themechange', sync);
    window.addEventListener('pageshow', sync);
    sync();
})();
