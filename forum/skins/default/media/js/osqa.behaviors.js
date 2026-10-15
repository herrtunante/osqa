/* Behaviour declared in the markup with data attributes. The
   Content-Security-Policy blocks inline event handlers (onclick="..." and the
   like) and javascript: URLs, so templates use these instead:

   <form data-confirm="Are you sure?">       asks before submitting
   <select data-autosubmit>                  submits its form when changed
   <button type="submit" data-form-target="_blank">
                                             submits the form into that window
                                             ("" for this one), e.g. a preview

   Loaded on every page (base_content.html, osqaadmin/djstyle_base.html);
   the listeners are on the document, so it may load deferred. */
(function () {
    'use strict';

    function closest(target, selector) {
        return target && target.closest ? target.closest(selector) : null;
    }

    document.addEventListener('submit', function (evt) {
        var form = closest(evt.target, 'form[data-confirm]');
        if (form && !window.confirm(form.getAttribute('data-confirm'))) {
            evt.preventDefault();
            evt.stopImmediatePropagation();
        }
    }, true);

    document.addEventListener('change', function (evt) {
        var select = closest(evt.target, 'select[data-autosubmit]');
        if (select && select.form) {
            select.form.submit();
        }
    });

    document.addEventListener('click', function (evt) {
        var button = closest(evt.target, '[data-form-target]');
        if (button && button.form) {
            button.form.target = button.getAttribute('data-form-target');
        }
    }, true);
})();
