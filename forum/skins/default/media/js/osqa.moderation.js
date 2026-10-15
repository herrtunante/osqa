/* The moderation inbox (osqaadmin/moderation_inbox.html).
 *
 * - "Delete as spam" opens its confirmation <dialog> (suspend the author,
 *   delete their other posts) instead of submitting at once. Without
 *   JavaScript the button submits with the dialog's default choices.
 * - j / k move the focus to the next / previous item, so a moderator can go
 *   through the inbox from the keyboard (Tab then reaches the item's buttons).
 */
(function () {
    'use strict';

    var inbox = document.getElementById('moderation-inbox');
    if (!inbox) {
        return;
    }

    inbox.addEventListener('click', function (event) {
        var opener = event.target.closest('[data-spam-dialog]');
        if (opener) {
            var dialog = document.getElementById(opener.getAttribute('data-spam-dialog'));
            if (dialog && typeof dialog.showModal === 'function' && !dialog.open) {
                event.preventDefault();
                dialog.showModal();
                var first = dialog.querySelector('input, button');
                if (first) {
                    first.focus();
                }
            }
            return;
        }
        var closer = event.target.closest('[data-dialog-close]');
        if (closer) {
            var open = closer.closest('dialog');
            if (open) {
                open.close();
            }
        }
    });

    document.addEventListener('keydown', function (event) {
        if (event.defaultPrevented || event.altKey || event.ctrlKey || event.metaKey) {
            return;
        }
        if (event.key !== 'j' && event.key !== 'k') {
            return;
        }
        var target = event.target;
        if (target.closest && target.closest('input, textarea, select, dialog, [contenteditable]')) {
            return;
        }
        var cards = Array.prototype.slice.call(inbox.querySelectorAll('.mod-card'));
        if (!cards.length) {
            return;
        }
        var index = cards.indexOf(target.closest ? target.closest('.mod-card') : null);
        index = event.key === 'j' ? Math.min(index + 1, cards.length - 1) : Math.max(index - 1, 0);
        event.preventDefault();
        cards[index].focus();
        cards[index].scrollIntoView({block: 'nearest'});
    });
}());
