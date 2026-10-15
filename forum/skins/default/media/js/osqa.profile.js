/*
 * Profile and settings pages (templates/user.html and users/*.html):
 * - the reputation graph (#diagram, its points in data-points), drawn with
 *   osqaTimeChart() in the skin's colours and drawn again when the device
 *   switches between light and dark;
 * - the suspension dialog: the number of days is asked only for "For X days";
 * - "Delete my account": the button works once the username is typed exactly;
 * - settings forms sent back with errors: the error summary gets the focus;
 * - tab bars that scroll by themselves (phones) show the current tab.
 */
(function ($) {
    'use strict';

    function drawReputationChart() {
        var $chart = $('#diagram[data-points]');
        if (!$chart.length || typeof window.osqaTimeChart !== 'function' || !$.plot) return;
        var points;
        try {
            points = JSON.parse($chart.attr('data-points'));
        } catch (e) {
            return;
        }
        var options = {series: {points: {show: points.length < 60}, lines: {fill: 0.12}}};
        window.osqaTimeChart($chart, points, options);

        if (window.matchMedia) {
            var scheme = window.matchMedia('(prefers-color-scheme: dark)');
            var redraw = function () {
                $chart.empty();
                window.osqaTimeChart($chart, points, options);
            };
            if (scheme.addEventListener) scheme.addEventListener('change', redraw);
        }
    }

    function deleteAccountConfirmation() {
        $('form.account-delete-form[data-confirm-username]').each(function () {
            var $form = $(this);
            var expected = $form.attr('data-confirm-username');
            var $name = $form.find('input[name=confirm_username]');
            var $password = $form.find('input[name=password]');
            var $button = $form.find('.account-delete-button');

            function update() {
                var ready = ($name.val() || '').trim() === expected && (!$password.length || $password.val() !== '');
                $button.prop('disabled', !ready);
            }

            $form.on('input change', 'input', update);
            update();
        });
    }

    $(document).on('change', '.dialog #bantype', function () {
        var $days = $(this).closest('.dialog').find('#forxdays');
        $days.prop('hidden', $(this).val() !== 'forxdays');
    });

    function showCurrentTabs() {
        $('.profile-tabs .tabsA, .settings-nav ul').each(function () {
            var current = $(this).find('[aria-current="page"]')[0];
            if (current && this.scrollWidth > this.clientWidth) {
                var left = current.getBoundingClientRect().left - this.getBoundingClientRect().left + this.scrollLeft;
                this.scrollLeft = Math.max(0, left - 16);
            }
        });
    }

    $(function () {
        showCurrentTabs();
        drawReputationChart();
        deleteAccountConfirmation();
        var $errors = $('#settings-errors');
        if ($errors.length) $errors.trigger('focus');
    });
})(jQuery);
