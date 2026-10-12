/*
 * Compatibility for the bundled third-party plugins (tag autocomplete, flot
 * charts, caret, image upload) written for jQuery 1.x. OSQA's own code uses
 * the jQuery 3 APIs directly.
 */
(function($) {
    // $.browser was removed in jQuery 1.9; the plugins only use it for IE/Opera workarounds.
    if (!$.browser) {
        $.browser = {msie: false, opera: false, mozilla: false, webkit: true, safari: false, version: '0'};
    }

    // .size() was removed in jQuery 3.0.
    if (!$.fn.size) {
        $.fn.size = function() { return this.length; };
    }

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
