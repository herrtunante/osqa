/**
 * Send the CSRF token with every same-site AJAX request in the X-CSRFToken header.
 * (jQuery only triggers global AJAX events on document.)
 */

$(document).on('ajaxSend', function(event, xhr, settings) {
    function getCookie(name) {
        var cookieValue = null;
        if (document.cookie && document.cookie != '') {
            var cookies = document.cookie.split(';');
            for (var i = 0; i < cookies.length; i++) {
                var cookie = cookies[i].trim();
                // Does this cookie string begin with the name we want?
                if (cookie.substring(0, name.length + 1) == (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }
    try {
        if (!(/^http:.*/.test(settings.url) || /^https:.*/.test(settings.url))) {
            // Only send the token to relative URLs i.e. locally.
            // With CSRF_USE_SESSIONS there is no csrftoken cookie; every page
            // carries the token in <meta name="csrf-token">.
            xhr.setRequestHeader("X-CSRFToken", $('meta[name=csrf-token]').attr('content') ||
                                 getCookie('csrftoken') || $('input[name=csrfmiddlewaretoken]').val());
        }
    } catch (e) {}
});
