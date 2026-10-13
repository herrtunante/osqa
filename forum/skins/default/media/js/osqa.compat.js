/*
 * Compatibility for the image upload code bundled in wmd.js, written for
 * jQuery 1.x. OSQA's own code uses the jQuery 3 APIs directly.
 */
(function($) {
    // jQuery.handleError was removed in jQuery 1.5; the upload plugin still calls it.
    if (!$.handleError) {
        $.handleError = function(s, xhr, status, e) {
            if (s.error) {
                s.error.call(s.context || s, xhr, status, e);
            }
            if (s.global) {
                $.event.trigger('ajaxError', [xhr, s, e]);
            }
        };
    }

})(jQuery);
