/*
 * List pages (question lists, tags index, users index):
 * - a filter box (input[data-list-filter]) that hides the items of the current
 *   page whose data-filter-text doesn't contain what is typed; Enter submits
 *   the form, which searches all pages on the server;
 * - question rows with one of the viewer's interesting or ignored tags are
 *   marked (classes is-interesting / is-ignored) from the sidebar tag selector;
 * - the tag selector's remove icons work from the keyboard.
 */
(function ($) {
    'use strict';

    function setupFilter(input) {
        var container = document.querySelector(input.getAttribute('data-list-filter'));
        var status = document.querySelector(input.getAttribute('data-status'));
        if (!container) return;
        var items = container.querySelectorAll('[data-filter-text]');
        var initialStatus = status ? status.innerHTML : '';
        var initialValue = input.value.trim().toLowerCase();

        function apply() {
            var value = input.value.trim().toLowerCase();
            var shown = 0;
            // The page already shows the server's results for this text.
            var filtering = value !== '' && value !== initialValue;
            items.forEach(function (item) {
                var match = !filtering || item.getAttribute('data-filter-text').indexOf(value) !== -1;
                item.hidden = !match;
                if (match) shown++;
            });
            if (status) {
                // Only changed when it has to, so screen readers don't repeat it on every key.
                var html = !filtering ? initialStatus : shown === 0 ? null : '';
                if (html === null) {
                    if (status.textContent !== input.getAttribute('data-none')) {
                        status.textContent = input.getAttribute('data-none');
                    }
                } else if (status.innerHTML !== html) {
                    status.innerHTML = html;
                }
            }
        }

        input.addEventListener('input', apply);
        input.addEventListener('keydown', function (event) {
            if (event.key === 'Escape' && input.value) {
                input.value = initialValue;
                apply();
            }
        });
    }

    function markedTagNames(kind) {
        var names = {};
        document.querySelectorAll('#tagSelector .tags.' + kind + ' .deletable-tag > a').forEach(function (link) {
            names[link.textContent.trim()] = true;
        });
        return names;
    }

    function markQuestionRows() {
        var interesting = markedTagNames('interesting'), ignored = markedTagNames('ignored');
        document.querySelectorAll('#listA .question-row').forEach(function (row) {
            var isInteresting = false, isIgnored = false;
            row.querySelectorAll('.tags a[rel=tag]').forEach(function (link) {
                var name = link.textContent.trim();
                link.classList.toggle('tag-interesting', !!interesting[name]);
                link.classList.toggle('tag-ignored', !!ignored[name]);
                isInteresting = isInteresting || !!interesting[name];
                isIgnored = isIgnored || !!ignored[name];
            });
            row.classList.toggle('is-interesting', isInteresting && !isIgnored);
            row.classList.toggle('is-ignored', isIgnored);
        });
    }

    function labelRemoveIcons() {
        var selector = document.getElementById('tagSelector');
        if (!selector) return;
        var label = selector.getAttribute('data-remove-label') || '';
        selector.querySelectorAll('.delete-icon').forEach(function (icon) {
            if (!icon.hasAttribute('role')) {
                var tag = icon.parentNode.querySelector('a');
                icon.setAttribute('role', 'button');
                icon.setAttribute('tabindex', '0');
                icon.setAttribute('alt', label + (tag ? ' ' + tag.textContent.trim() : ''));
            }
        });
    }

    $(function () {
        document.querySelectorAll('input[data-list-filter]').forEach(setupFilter);
        markQuestionRows();
        labelRemoveIcons();
        // osqa.main.js triggers this after adding or removing a marked tag.
        $(document).on('contentchanged', '#tagSelector .tags', function () {
            labelRemoveIcons();
            markQuestionRows();
        });
        $(document).on('keydown', '#tagSelector .delete-icon', function (event) {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                $(this).trigger('click');
            }
        });
    });
})(jQuery);
