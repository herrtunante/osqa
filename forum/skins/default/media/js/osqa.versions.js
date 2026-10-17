/*
 * "Applies to version" on the ask and edit question pages
 * (templates/compose/versions.html, forum/utils/post_versions.py).
 *
 * There is a select for every tag that has versions; a select is shown while
 * its tag (or one of the tag's synonyms) is in the tags field, and the whole
 * fieldset is hidden when none is. The tags come from the input named
 * "tags": the hidden input osqa.tagchips.js keeps up to date (it fires
 * `input`), or the plain text box without it. Without JavaScript the
 * server shows the selects of the tags the form was opened with.
 */
(function () {
    function tagsOf(form) {
        var input = form.querySelector('input[name="tags"]');
        if (!input) return null;
        return input.value.toLowerCase().split(/[\s,]+/).filter(function (t) { return t; });
    }

    function update(fieldset) {
        var form = fieldset.closest('form');
        var tags = form && tagsOf(form);
        if (!tags) return;
        var any = false;
        fieldset.querySelectorAll('.version-choice').forEach(function (choice) {
            var names = (choice.getAttribute('data-tag-names') || '').split(' ');
            var on = names.some(function (name) { return name && tags.indexOf(name) >= 0; });
            choice.hidden = !on;
            any = any || on;
        });
        fieldset.hidden = !any;
    }

    function setup(fieldset) {
        var form = fieldset.closest('form');
        if (!form) return;
        var listener = function (event) {
            if (event.target && event.target.name === 'tags') update(fieldset);
        };
        form.addEventListener('input', listener);
        form.addEventListener('change', listener);
        update(fieldset);
    }

    function init() {
        document.querySelectorAll('.compose-versions[data-follow-tags]').forEach(setup);
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }
})();
