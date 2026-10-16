/*
 * The search page's filters (search.html): every control belongs to the
 * search form (form="search-form"), so the query, the filters and the sort
 * travel together in the URL: a plain GET, shareable, and the back button
 * returns to the previous filters.
 *
 *   - empty fields are left out of the URL (and from/to unless the date is
 *     "Custom range", whose fields show only then);
 *   - on a desktop a radio button, checkbox or select applies at once;
 *   - on phones and narrow windows (the sidebar is below the results) the
 *     "Filters" button opens them in a modal <dialog>, where they apply with
 *     "Apply filters"; the panel moves into the dialog and back;
 *   - tags and author get suggestions ($.fn.osqaAutocomplete).
 *
 * Without JavaScript the panel is the sidebar (below the results on phones)
 * and "Apply filters" submits it.
 */
(function ($) {
    'use strict';

    var form = document.getElementById('search-form');
    var panel = document.getElementById('search-filters');
    if (!form || !panel) {
        return;
    }
    var home = document.getElementById('search-filters-home');
    var dialog = document.getElementById('search-filters-dialog');
    var dialogBody = dialog && dialog.querySelector('.search-filters-dialog-body');
    var toggle = document.querySelector('.search-filters-toggle');
    var date = document.getElementById('search-filter-date');
    var range = document.getElementById('search-filter-range');
    var narrow = window.matchMedia('(max-width: 960px)');

    function submit() {
        if (form.requestSubmit) {
            form.requestSubmit();
        } else {
            form.submit();
        }
    }

    function inDialog() {
        return !!(dialog && dialog.open);
    }

    // -- the URL ------------------------------------------------------------

    // The form shows what the URL says: after "back" the browser may restore
    // the values chosen before leaving the page (or the whole page, bfcache).
    form.reset();
    window.addEventListener('pageshow', function (event) {
        if (event.persisted) {
            form.reset();
            showRange();
        }
    });

    form.addEventListener('formdata', function (event) {
        var data = event.formData, entries = [];
        var custom = data.get('date') === 'custom';
        data.forEach(function (value, key) {
            entries.push([key, typeof value === 'string' ? value.replace(/\s+/g, ' ').trim() : value]);
        });
        entries.forEach(function (entry) {
            data.delete(entry[0]);
        });
        entries.forEach(function (entry) {
            if (entry[1] !== '' && (custom || (entry[0] !== 'from' && entry[0] !== 'to'))) {
                data.append(entry[0], entry[1]);
            }
        });
    });

    // -- date range ---------------------------------------------------------

    function showRange() {
        if (date && range) {
            range.hidden = date.value !== 'custom';
        }
    }
    showRange();

    panel.addEventListener('change', function (event) {
        var target = event.target;
        if (target === date) {
            showRange();
            if (date.value === 'custom') {
                var start = document.getElementById('search-filter-from');
                if (start) {
                    start.focus();
                }
                return;
            }
        }
        if (!inDialog() && target.matches('input[type=radio], input[type=checkbox], select')) {
            submit();
        }
    });

    // -- suggestions --------------------------------------------------------

    if ($ && $.fn.osqaAutocomplete) {
        var $tags = $('#search-filter-tags'), $author = $('#search-filter-author');
        $tags.osqaAutocomplete($tags.attr('data-autocomplete-url'), {multiple: true});
        $author.osqaAutocomplete($author.attr('data-autocomplete-url'), {
            label: function (row) { return row[3] + ' (' + row[2] + ')'; },
            value: function (row) { return row[3]; },
            select: function () {
                if (!inDialog()) {
                    submit();
                }
            }
        });
    }

    // The suggestion menus belong in the dialog while it is open (top layer).
    function menusIn(container) {
        if (!$) {
            return;
        }
        if (!$.fn.autocomplete) {
            // jQuery UI still loading (osqa.autocomplete.js): move them once it is there.
            if (window.osqaJqueryUI) {
                window.osqaJqueryUI().then(function () {
                    if ($.fn.autocomplete) {
                        menusIn(inDialog() ? dialogBody : null);
                    }
                }, function () {});
            }
            return;
        }
        $('#search-filter-tags, #search-filter-author').each(function () {
            var $input = $(this);
            if ($input.autocomplete('instance')) {
                $input.autocomplete('option', 'appendTo', container);
            }
        });
    }

    // -- the dialog on narrow screens ------------------------------------------

    if (!dialog || !dialogBody || !toggle || typeof dialog.showModal !== 'function') {
        return;
    }
    toggle.hidden = false;
    if (home) {
        home.classList.add('is-enhanced');
    }

    toggle.addEventListener('click', function () {
        dialogBody.appendChild(panel);
        menusIn(dialogBody);
        dialog.showModal();
    });

    dialog.addEventListener('close', function () {
        if (home) {
            home.appendChild(panel);
        }
        menusIn(null);
        if (toggle.offsetParent !== null) {
            toggle.focus();
        }
    });

    dialog.addEventListener('click', function (event) {
        // The close button, or a click on the backdrop (outside the box).
        if (event.target.closest('[data-search-filters-close]') || event.target === dialog) {
            dialog.close();
        }
    });

    // Back to a wide window: the filters return to the sidebar.
    var onResize = function (event) {
        if (!event.matches && dialog.open) {
            dialog.close();
        }
    };
    if (narrow.addEventListener) {
        narrow.addEventListener('change', onResize);
    }
})(window.jQuery);
