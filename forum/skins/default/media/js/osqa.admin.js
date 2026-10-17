/* The admin menu is a <details> disclosure: open on wide screens, folded on
   phones (called right after the element, so it never flashes open). */
function osqaAdminMenu(details) {
    if (!details || !window.matchMedia) {
        return;
    }
    var wide = window.matchMedia('(min-width: 768px)');
    function update() {
        details.open = wide.matches;
    }
    update();
    if (wide.addEventListener) {
        wide.addEventListener('change', update);
    }
}

$(function() {
    $(document).on('click', '.string_list_widget_button', function() {
        var $but = $(this);

        if ($but.is('.add')) {
            var $remove = $('<button type="button" class="string_list_widget_button remove">\u2212</button>')
                .attr('aria-label', $but.attr('data-remove-label') || 'Remove')
                .attr('title', $but.attr('data-remove-label') || 'Remove');
            var $new = $('<div class="string-list-input" style="display: none;"></div>')
                .append($('<input type="text" value="" />').attr('name', $but.attr('name'))
                    .attr('aria-label', $but.attr('data-item-label') || ''))
                .append($remove);

            $but.parent().before($new);
            $new.slideDown('fast', function() {
                $new.css('display', '');
                $new.find('input').trigger('focus');
            });
        } else {
            $but.parent().slideUp('fast', function() {
                $but.parent().remove();
            });
        }

        return false;
    })

    $('.fieldtool').each(function() {
        var $link = $(this);
        var $input = $link.parent().parent().find('input, textarea');
        var name = $input.attr('name')

        if ($link.is('.context')) {
            $link.click(function() {
                var $contextbox = $('<input type="text" value="' + name + '" />');
                $link.replaceWith($contextbox);
            });
        } else if ($link.is('.default')) {
            if ($input.length == 1 && ($input.is('[type=text]') || $input.is('textarea'))) {
                $link.click(function() {
                    $.post(name + '/', function(data) {
                        $input.val(data);
                    });
                });
            } else {
                $link.attr('href', name + '/');
            }
        }
    });

    $('.url_field').each(function() {
        var $input = $(this);
        var $anchor = $input.parent().find('.url_field_anchor');
        var app_url = $anchor.attr('href');

        function rewrite_anchor() {
            var val = app_url + '/' +  $input.val();

            $anchor.attr('href', val);
            $anchor.html(val);

        }

        $input.keyup(rewrite_anchor);
        rewrite_anchor();        
    });

    $('#test_email_settings a.test_button').on('click', function(evt) {
        evt.preventDefault();
        $('div.test_status').hide('slow')
        $('div.ajax_indicator').show('fast')
        $.post($(this).attr('href'), function(data) {
            $('div.test_status').html(data)
        }).fail(function(xhr) {
            $('div.test_status').text('HTTP ' + xhr.status + ' ' + (xhr.statusText || ''))
        }).always(function() {
            $('div.ajax_indicator').hide('fast')
            $('div.test_status').show('slow')
        })
    })
});
