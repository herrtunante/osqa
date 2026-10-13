var currentSideBar = 'div#title_side_bar';
function changeSideBar(enabled_bar) {
    if (enabled_bar != currentSideBar) {
        $(currentSideBar).hide();
        currentSideBar = enabled_bar;
        $(currentSideBar).fadeIn('slow');
    }

}

$(function () {
    $('div#editor_side_bar').hide();
    $('div#tags_side_bar').hide();

    $('#id_title').focus(function(){changeSideBar('div#title_side_bar')});
    $('#editor').focus(function(){changeSideBar('div#editor_side_bar')});
    $('#id_tags').focus(function(){changeSideBar('div#tags_side_bar')});
});

function escapeHtml(value) {
    return String(value).replace(/[&<>"']/g, function(c) {
        return {'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[c];
    });
}

$(function() {
    var $input = $('#id_title');
    var $box = $('#ask-related-questions');
    var template = $('#question-summary-template').html();
    var $editor = $('#editor');

    var results_cache = {};

    function reload_suggestions_box(e) {
        var relatedQuestionsDiv = $('#ask-related-questions');
        var q = $input.val().replace(/^\s+|\s+$/g,"");

        if(q.length == 0) {
            close_suggestions_box();
            relatedQuestionsDiv.html('');
            return false;
        } else if(relatedQuestionsDiv[0].style.height == 0 || relatedQuestionsDiv[0].style.height == '0px') {
            relatedQuestionsDiv.animate({'height':'150'}, 350);
        }

        if (results_cache[q] && results_cache[q] != '') {
            relatedQuestionsDiv.html(results_cache[q]);
            return false;
        }

        $.post(related_questions_url, {title: q}, function(data) {
            if (data) {
                var c = $input.val().replace(/^\s+|\s+$/g,"");

                if (c != q) {
                    return;
                }

                if(data.length == 0) {
                    relatedQuestionsDiv.html('<br /><br /><div align="center">No questions like this have been found.</div>');
                    return;
                }

                var html = '';
                for (var i = 0; i < data.length; i++) {
                    // Titles and summaries are user text: escape before templating.
                    var item = template.replace(new RegExp('%URL%', 'g'), escapeHtml(data[i].url))
                                       .replace(new RegExp('%SCORE%', 'g'), escapeHtml(data[i].score))
                                       .replace(new RegExp('%TITLE%', 'g'), escapeHtml(data[i].title))
                                       .replace(new RegExp('%SUMMARY%', 'g'), escapeHtml(data[i].summary));

                    html += item;

                }

                results_cache[q] = html;

                relatedQuestionsDiv.html(html);
            }
        }, 'json');

        return false;
    }

    function close_suggestions_box() {
        $('#ask-related-questions').animate({'height':'0'},350, function() {
            $('#ask-related-questions').html('');
        });
    }

    var suggestions_timer = null;
    $input.on('keyup focus', function() {
        clearTimeout(suggestions_timer);
        suggestions_timer = setTimeout(reload_suggestions_box, 300);
    });

    $editor.change(function() {
        if ($editor.html().length > 10) {
            close_suggestions_box();
        }
    });



    // for chrome
    $input.keydown(focus_on_question);
    function focus_on_question(e) {
        var is_chrome = navigator.userAgent.toLowerCase().indexOf('chrome') > -1;

        if(e.keyCode == 9 && is_chrome) {
            $('#editor')[0].focus();
        }
    }
});

// AI duplicate detection (forum_modules/ai): after a pause in typing the title,
// ask the server which existing questions ask the same thing.
$(function() {
    var url = window.ai_duplicates_url;
    var $title = $('#id_title'), $box = $('#ask-duplicates');
    if (!url || !$title.length || !$box.length) return;

    var timer = null, last = '', request = 0, dismissed = {};

    function render(title, duplicates) {
        if (!duplicates.length || dismissed[title]) {
            $box.prop('hidden', true).empty();
            return;
        }
        var html = '<div class="ask-duplicates-head"><strong>' + escapeHtml(messages.ai_duplicates_title) +
                   '</strong><button type="button" class="ask-duplicates-dismiss plain-button">' +
                   escapeHtml(messages.ai_duplicates_dismiss) + '</button></div><ul>';
        $.each(duplicates, function(i, d) {
            var status = d.accepted ? messages.ai_duplicates_accepted :
                         (d.answers == 1 ? messages.ai_duplicates_answer : messages.ai_duplicates_answers.replace('%s', d.answers));
            html += '<li><a href="' + escapeHtml(d.url) + '" target="_blank" rel="noopener">' + escapeHtml(d.title) + '</a>' +
                    ' <span class="ask-duplicates-status' + (d.accepted ? ' accepted' : '') + '">' + escapeHtml(status) + '</span>' +
                    '<div class="ask-duplicates-reason">' + escapeHtml(d.reason) + '</div></li>';
        });
        $box.html(html + '</ul><p class="ask-duplicates-note">' + escapeHtml(messages.ai_duplicates_note) + '</p>')
            .prop('hidden', false);
    }

    function check() {
        var title = $.trim($title.val());
        if (title === last) return;
        last = title;
        var id = ++request;
        if (title.length < 15 || title.split(/\s+/).length < 3) {
            render(title, []);
            return;
        }
        $.post(url, {title: title, text: $('#editor').val() || ''}).done(function(data) {
            if (id === request) render(title, data.duplicates || []);
        });
    }

    $title.on('input', function() {
        clearTimeout(timer);
        timer = setTimeout(check, 1200);
    }).on('blur', check);

    $box.on('click', '.ask-duplicates-dismiss', function() {
        dismissed[last] = true;
        $box.prop('hidden', true).empty();
    });
});
