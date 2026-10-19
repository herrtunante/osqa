/*
 * Settings > View: "Embed YouTube videos". Clicking a YouTube link inside a
 * post plays the video below the link (click again to close).
 */
$(function() {
    function videoId(href) {
        var m = href.match(/(?:youtube\.com\/(?:watch\?(?:.*&)?v=|embed\/|shorts\/|v\/)|youtu\.be\/)([\w-]{11})/);
        return m ? m[1] : null;
    }

    $(document).on('click', '.question-body a, .answer-body a', function(evt) {
        var id = videoId(this.href || '');
        if (!id || evt.ctrlKey || evt.metaKey || evt.shiftKey || evt.button !== 0) {
            return;
        }
        evt.preventDefault();

        var $link = $(this);
        var $player = $link.data('player');
        if ($player) {
            $player.remove();
            $link.removeData('player');
            return;
        }

        $player = $('<div class="video-embed"><iframe allowfullscreen loading="lazy" ' +
            'allow="accelerometer; autoplay; encrypted-media; gyroscope; picture-in-picture"></iframe></div>');
        $player.find('iframe')
            .attr('src', 'https://www.youtube-nocookie.com/embed/' + id + '?autoplay=1')
            .attr('title', $link.text());
        $link.data('player', $player).after($player);
    });
});
