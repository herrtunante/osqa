from datetime import datetime, timedelta

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Exists, OuterRef

from forum.models import Question, Answer, AISuggestion


class Command(BaseCommand):
    help = ("Adds a clearly marked AI-suggested answer, with sources, to questions still unanswered after "
            "Admin > AI assistance > 'Suggest an answer after'. Run it hourly from cron.")

    def add_arguments(self, parser):
        parser.add_argument('--limit', type=int, default=20, help="Questions to handle per run (default 20).")
        parser.add_argument('--max-age-days', type=int, default=30,
                            help="Leave older unanswered questions alone (default 30).")

    def handle(self, limit=20, max_age_days=30, **options):
        try:
            from forum_modules.ai.assistant import answer_question
            from forum_modules.ai.client import is_configured
            from forum_modules.ai.settings import AI_SUGGEST_ANSWERS, AI_SUGGEST_AFTER_HOURS, AI_MODEL
        except ImportError:
            raise CommandError("The ai module is disabled or its requirements are missing.")
        if not bool(AI_SUGGEST_ANSWERS):
            self.stdout.write("Suggested answers are turned off.")
            return
        if not is_configured():
            raise CommandError("The ai module has no Gemini API key or Vertex AI project configured.")

        now = datetime.now()
        answered = Answer.objects.filter(parent=OuterRef('pk')).filter_state(deleted=False)
        questions = Question.objects.filter_state(deleted=False, closed=False).filter(
            added_at__lt=now - timedelta(hours=int(AI_SUGGEST_AFTER_HOURS)),
            added_at__gt=now - timedelta(days=max_age_days),
        ).exclude(Exists(answered)).exclude(
            Exists(AISuggestion.objects.filter(question=OuterRef('pk')))
        ).order_by('added_at')[:limit]

        added = declined = failed = 0
        for question in questions:
            revision = question.active_revision
            result = answer_question(question.title, revision.body if revision else question.body)
            if result is None:
                failed += 1  # try again next run
                continue
            if result['insufficient']:
                # Remembered (and not shown) so the question isn't retried every hour.
                AISuggestion.objects.create(question=question, status=AISuggestion.DECLINED, body=result['text'],
                                            model=str(AI_MODEL))
                declined += 1
                continue
            AISuggestion.objects.create(question=question, body=result['text'], html=result['html'],
                                        sources=result['sources'], model=str(AI_MODEL))
            added += 1

        self.stdout.write("Suggested answers: %d added, %d without enough information, %d failed."
                          % (added, declined, failed))
