/*
 * Post forms (ask, edit question, edit answer, retag: form.compose-form; and
 * the answer form on the question page, #fmanswer).
 *
 * - the error summary gets the focus after a refused submission;
 * - Ctrl+Enter (Cmd+Enter on a Mac) posts; Enter in a one-line field doesn't;
 * - "You will lose your changes" when leaving with unsaved work
 *   (data-guard="filled": anything typed; "changed": anything changed),
 *   never after the form was sent;
 * - the live preview: below the editor on a desktop (with Hide/Show), as a
 *   Write/Preview tab pair on a phone;
 * - picking a revision reloads the form with it.
 */
(function () {
    var isMac = /Mac|iPhone|iPad/.test(navigator.platform || navigator.userAgent);

    function fieldsOf(form) {
        return Array.prototype.filter.call(form.elements, function (el) {
            return el.name && el.name !== 'csrfmiddlewaretoken' && el.type !== 'submit' && el.type !== 'button' &&
                   el.name !== 'revision' && !/^(form_token|bot_)/.test(el.name);
        });
    }

    function snapshot(form) {
        return fieldsOf(form).map(function (el) {
            return el.type === 'checkbox' ? String(el.checked) : el.value;
        }).join('\u0000');
    }

    function hasTyped(form) {
        var entry = form.querySelector('.tag-chips-entry');
        return fieldsOf(form).some(function (el) {
            return el.type !== 'checkbox' && el.type !== 'hidden' && el.value.trim() ||
                   el.id === 'id_tags_value' && el.value.trim();
        }) || !!(entry && entry.value.trim());
    }

    function setupForm(form) {
        var submitting = false;
        var guard = form.getAttribute('data-guard');
        var initial = snapshot(form);
        var submit = form.querySelector('.compose-submit') || form.querySelector('input.submit');

        if (isMac) {
            form.querySelectorAll('.compose-mod').forEach(function (k) { k.textContent = '⌘'; });
        }

        function send() {
            if (submitting) return;
            if (form.id === 'fmanswer') {
                // question.html sends it from the button's click handler.
                submit && submit.click();
                return;
            }
            form.dispatchEvent(new Event('compose:beforesubmit'));
            if (form.requestSubmit) {
                form.requestSubmit(submit || undefined);
            } else if (submit) {
                submit.click();
            }
        }

        form.addEventListener('keydown', function (e) {
            if (e.key !== 'Enter' || e.isComposing) return;
            if (e.ctrlKey || e.metaKey) {
                e.preventDefault();
                send();
            } else if (e.target.matches('#id_title, #id_summary')) {
                // Posting is a deliberate act: the button or Ctrl+Enter.
                e.preventDefault();
            }
        });

        form.addEventListener('submit', function (e) {
            submitting = true;
            var sender = e.submitter;
            // Disabled after the browser has read the button's value.
            setTimeout(function () {
                if (sender && sender.classList.contains('compose-submit')) sender.disabled = true;
            }, 0);
            // Pressing Back to this page shows it again: allow sending again.
            window.addEventListener('pageshow', function (ev) {
                if (ev.persisted) {
                    submitting = false;
                    if (sender) sender.disabled = false;
                }
            }, {once: true});
        });
        if (form.id === 'fmanswer' && submit) {
            submit.addEventListener('click', function () { submitting = true; }, true);
        }

        if (guard) {
            window.addEventListener('beforeunload', function (e) {
                if (submitting) return;
                var dirty = guard === 'filled' ? hasTyped(form) : snapshot(form) !== initial;
                if (dirty) {
                    e.preventDefault();
                    e.returnValue = '';
                    return '';
                }
            });
        }

        // Revision picker: reload the form with the chosen revision.
        var revision = form.querySelector('#id_revision');
        var pick = form.querySelector('#select_revision');
        if (revision && pick) {
            pick.hidden = true;
            revision.addEventListener('change', function () {
                submitting = true;
                pick.click();
            });
        }
    }

    function setupPreview() {
        var preview = document.getElementById('previewer');
        var page = document.querySelector('.compose-page');
        if (!preview || !page) return;
        var toggle = document.getElementById('pre-collapse');
        var pane = document.getElementById('compose-preview');
        var write = document.getElementById('compose-write');
        var tabs = Array.prototype.slice.call(page.querySelectorAll('.compose-tab'));
        var phone = window.matchMedia('(max-width: 640px)');
        var shown = true;

        if (toggle) {
            toggle.addEventListener('click', function () {
                shown = !shown;
                preview.hidden = !shown;
                toggle.textContent = toggle.getAttribute(shown ? 'data-hide' : 'data-show');
                toggle.setAttribute('aria-expanded', shown ? 'true' : 'false');
            });
        }

        function select(tab, focus) {
            tabs.forEach(function (t) {
                var on = t === tab;
                t.setAttribute('aria-selected', on ? 'true' : 'false');
                t.tabIndex = on ? 0 : -1;
                document.getElementById(t.getAttribute('aria-controls')).hidden = !on;
            });
            page.classList.toggle('is-previewing', tab.id === 'compose-tab-preview');
            if (focus) tab.focus();
        }

        function layout() {
            if (phone.matches) {
                [write, pane].forEach(function (p) {
                    p.setAttribute('role', 'tabpanel');
                    p.setAttribute('aria-labelledby', p === write ? 'compose-tab-write' : 'compose-tab-preview');
                });
                preview.hidden = false;
                select(tabs.filter(function (t) { return t.getAttribute('aria-selected') === 'true'; })[0] || tabs[0]);
            } else {
                [write, pane].forEach(function (p) {
                    p.removeAttribute('role');
                    p.removeAttribute('aria-labelledby');
                    p.hidden = false;
                });
                preview.hidden = !shown;
                page.classList.remove('is-previewing');
            }
        }

        tabs.forEach(function (tab, index) {
            tab.addEventListener('click', function () { select(tab); });
            tab.addEventListener('keydown', function (e) {
                var next = {ArrowRight: 1, ArrowLeft: -1}[e.key];
                if (!next) return;
                e.preventDefault();
                select(tabs[(index + next + tabs.length) % tabs.length], true);
            });
        });
        if (phone.addEventListener) phone.addEventListener('change', layout);
        layout();
    }

    function setup() {
        var errors = document.getElementById('form-errors');
        if (errors) errors.focus();
        document.querySelectorAll('form.compose-form, form#fmanswer').forEach(setupForm);
        setupPreview();
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', setup);
    } else {
        setup();
    }
})();
