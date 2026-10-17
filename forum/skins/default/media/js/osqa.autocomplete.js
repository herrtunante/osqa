/*
 * Tag and user suggestions, built on jQuery UI autocomplete.
 *
 *   $('#id_tags').osqaAutocomplete(url, {
 *       multiple: true,                  // space separated values (tag fields)
 *       label: function (row) { ... },   // text shown in the menu
 *       value: function (row) { ... },   // text put into the field
 *       select: function (row) { ... }   // called with the chosen row
 *   });
 *
 * The matching_tags / matching_users views answer one "id|name|count|..." row
 * per line; rows are passed around as arrays of those columns. A tag row
 * found through a synonym has the synonym as its fourth column: the default
 * label shows "csv → export (12)" and the value is the tag, "export".
 *
 * jQuery UI (script and style sheet, the data-jquery-ui and data-jquery-ui-css
 * attributes of this script's tag) is loaded only when a page uses it:
 * window.osqaJqueryUI() returns a promise of it (also used by the award
 * points slider). osqaAutocomplete() loads it when one of the fields is first
 * focused (or typed in), then sets the fields up and looks up what is already
 * typed; where jQuery UI is already loaded (the admin), the fields are set up
 * at once.
 */
(function ($) {
    var script = document.currentScript;
    var loading = null;

    function load(tag, attributes) {
        return new Promise(function (resolve, reject) {
            var element = document.createElement(tag);
            Object.keys(attributes).forEach(function (name) { element[name] = attributes[name]; });
            element.onload = function () { resolve(); };
            element.onerror = function () { reject(new Error(tag + ' not loaded')); };
            document.head.appendChild(element);
        });
    }

    window.osqaJqueryUI = function () {
        if (!loading) {
            if ($.ui && $.fn.autocomplete) {
                loading = Promise.resolve();
            } else {
                var js = script && script.getAttribute('data-jquery-ui');
                var css = script && script.getAttribute('data-jquery-ui-css');
                if (!js) {
                    return Promise.reject(new Error('no jQuery UI'));
                }
                loading = Promise.all([
                    css ? load('link', {rel: 'stylesheet', href: css}) : Promise.resolve(),
                    load('script', {src: js, async: false})
                ]).catch(function (error) {
                    loading = null;
                    throw error;
                });
            }
        }
        return loading;
    };

    function lastWord(text) {
        return text.split(/\s+/).pop();
    }

    $.fn.osqaAutocomplete = function (url, options) {
        if (!this.length) {
            return this;
        }
        if (!$.fn.autocomplete) {
            var $fields = this;
            $fields.on('focus.osqaAutocomplete input.osqaAutocomplete', function () {
                $fields.off('.osqaAutocomplete');
                window.osqaJqueryUI().then(function () {
                    if (!$.fn.autocomplete) {
                        return;
                    }
                    $fields.osqaAutocomplete(url, options);
                    var active = document.activeElement;
                    if (active && $fields.is(active) && active.value) {
                        $(active).autocomplete('search');
                    }
                }, function () { /* fields without suggestions */ });
            });
            return this;
        }
        var o = $.extend({
            multiple: false,
            max: 10,
            label: function (row) { return (row[3] ? row[3] + ' \u2192 ' : '') + row[1] + ' (' + row[2] + ')'; },
            value: function (row) { return row[1]; },
            select: null
        }, options);

        return this.each(function () {
            var $input = $(this), pending = null;

            $input.attr('autocomplete', 'off').autocomplete({
                minLength: 1,
                delay: 250,
                classes: {'ui-autocomplete': 'osqa-autocomplete'},
                source: function (request, response) {
                    var term = o.multiple ? lastWord(request.term) : request.term;
                    if (!term) {
                        response([]);
                        return;
                    }
                    if (pending) pending.abort();
                    pending = $.get(url, {q: term, limit: o.max}).done(function (text) {
                        response($.map(text.split('\n'), function (line) {
                            line = line.trim();
                            if (!line) return null;
                            var row = line.split('|');
                            return {label: o.label(row), value: o.value(row), row: row};
                        }));
                    }).fail(function (xhr, status) {
                        if (status !== 'abort') response([]);
                    });
                },
                // Moving through the menu must not overwrite the earlier tags.
                focus: function () {
                    return !o.multiple;
                },
                select: function (event, ui) {
                    if (o.multiple) {
                        var words = this.value.split(/\s+/);
                        words.pop();
                        words.push(ui.item.value);
                        this.value = words.join(' ') + ' ';
                    } else {
                        this.value = ui.item.value;
                    }
                    if (o.select) o.select.call(this, ui.item.row);
                    return false;
                }
            });
        });
    };
})(jQuery);
