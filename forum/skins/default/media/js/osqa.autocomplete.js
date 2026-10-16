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
 */
(function ($) {
    function lastWord(text) {
        return text.split(/\s+/).pop();
    }

    $.fn.osqaAutocomplete = function (url, options) {
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
