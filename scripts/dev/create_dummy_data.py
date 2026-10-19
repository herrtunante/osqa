#!/usr/bin/env python
"""
Create dummy data for OSQA Django 4.2
Creates users, questions, answers, comments, tags, votes, etc.
"""
import os
import django
import random
from datetime import datetime, timedelta

# Run from the repository root: 'settings' and the importData/ and osqa.db
# paths below are resolved from the current directory.
import sys
sys.path.insert(0, os.getcwd())
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'settings')
django.setup()

from django.conf import settings as django_settings
if not django_settings.DEBUG:
    sys.exit('Refusing to run with DEBUG = False: this creates users with known passwords.')

from forum.models import User, Tag, Vote, KeyValue
from forum.models import Question, Answer, Comment

print("="*80)
print("OSQA Dummy Data Generator")
print("="*80)

# Sample data
FIRST_NAMES = ['John', 'Jane', 'Bob', 'Alice', 'Charlie', 'Diana', 'Eve', 'Frank', 'Grace', 'Henry']
LAST_NAMES = ['Smith', 'Johnson', 'Williams', 'Brown', 'Jones', 'Garcia', 'Miller', 'Davis', 'Rodriguez', 'Martinez']

TAGS_DATA = [
    ('python', 'Python programming language'),
    ('django', 'Django web framework'),
    ('javascript', 'JavaScript programming'),
    ('html', 'HTML markup language'),
    ('css', 'Cascading Style Sheets'),
    ('sql', 'SQL and databases'),
    ('git', 'Version control with Git'),
    ('linux', 'Linux operating system'),
    ('docker', 'Docker containerization'),
    ('react', 'React JavaScript library'),
]

QUESTIONS = [
    {
        'title': 'How to upgrade Django from 1.6 to 4.2?',
        'body': 'I have an old Django 1.6 project and want to upgrade to Django 4.2. What are the main steps and gotchas I should be aware of?',
        'tags': ['django', 'python'],
    },
    {
        'title': 'Best practices for Git branching strategy',
        'body': 'What branching strategy do you recommend for a team of 5 developers? Should we use Git Flow, GitHub Flow, or something else?',
        'tags': ['git'],
    },
    {
        'title': 'How to center a div with CSS?',
        'body': 'I\'ve been struggling to center a div both horizontally and vertically. What\'s the modern way to do this with CSS?',
        'tags': ['css', 'html'],
    },
    {
        'title': 'Python list comprehension vs map()',
        'body': 'When should I use list comprehensions versus the map() function in Python? Is one more performant than the other?',
        'tags': ['python'],
    },
    {
        'title': 'Docker vs Virtual Machines',
        'body': 'What are the key differences between Docker containers and traditional virtual machines? When should I use one over the other?',
        'tags': ['docker', 'linux'],
    },
    {
        'title': 'React hooks vs class components',
        'body': 'Should I use React hooks or class components for new projects in 2025? What are the advantages of hooks?',
        'tags': ['react', 'javascript'],
    },
    {
        'title': 'SQL JOIN types explained',
        'body': 'Can someone explain the difference between INNER JOIN, LEFT JOIN, RIGHT JOIN, and FULL OUTER JOIN with examples?',
        'tags': ['sql'],
    },
    {
        'title': 'JavaScript async/await vs Promises',
        'body': 'I\'m confused about when to use async/await versus .then() with Promises. Are they interchangeable?',
        'tags': ['javascript'],
    },
]

ANSWERS = [
    'Here\'s a comprehensive answer based on my experience...',
    'I had the same problem and solved it by...',
    'The documentation says...',
    'Try this approach:',
    'After researching this, I found that...',
    'The accepted answer is good, but I\'d like to add...',
    'This worked for me:',
    'According to the official docs...',
]

COMMENTS = [
    'Thanks, this helped!',
    'Could you explain more about...?',
    'This doesn\'t work in my case',
    'Great answer!',
    'I think you meant to say...',
    'Works perfectly, thank you!',
    'Upvoted!',
    'This is exactly what I needed',
]

def create_users(count=15):
    """Create dummy users including alfonso admin"""
    print(f"\n[1/7] Creating {count} users...")

    users = []

    # Create admin user alfonso
    admin, created = User.objects.get_or_create(
        username='alfonso',
        defaults={
            'email': 'alfonso@example.com',
            'first_name': 'Alfonso',
            'last_name': 'Administrator',
            'is_staff': True,
            'is_superuser': True,
            'is_active': True,
        }
    )
    admin.set_password('alfonso')
    admin.save()

    if created:
        print("  ✓ Created admin: alfonso (password: alfonso)")
    else:
        print("  ✓ Updated admin: alfonso (password: alfonso)")

    users.append(admin)

    # Create regular users
    for i in range(count - 1):
        username = f"user{i+1}"
        user, created = User.objects.get_or_create(
            username=username,
            defaults={
                'email': f'{username}@example.com',
                'first_name': random.choice(FIRST_NAMES),
                'last_name': random.choice(LAST_NAMES),
                'is_active': True,
            }
        )
        user.set_password('password123')
        user.save()

        if created:
            print(f"  ✓ Created user: {username}")

        users.append(user)

    return users

def create_tags():
    """Create tags"""
    print(f"\n[2/7] Creating {len(TAGS_DATA)} tags...")

    tags = []
    for tag_name, description in TAGS_DATA:
        tag, created = Tag.objects.get_or_create(
            name=tag_name,
            defaults={
                'created_by': User.objects.first(),
                'created_at': datetime.now() - timedelta(days=random.randint(30, 365)),
                'used_count': 0,
            }
        )
        if created:
            print(f"  ✓ Created tag: {tag_name}")
        tags.append(tag)

    return tags

def create_questions(users, tags_list):
    """Create questions"""
    print(f"\n[3/7] Creating {len(QUESTIONS)} questions...")

    questions = []
    for i, q_data in enumerate(QUESTIONS):
        author = random.choice(users)

        # Create question using Question model
        question = Question(
            title=q_data['title'],
            body=q_data['body'],
            tagnames=' '.join(q_data['tags']),
            author=author,
            added_at=datetime.now() - timedelta(days=random.randint(1, 30)),
            score=random.randint(0, 15),
            state_string='(published)',
        )
        question.save()

        # Add tags to question
        for tag_name in q_data['tags']:
            tag = Tag.objects.get(name=tag_name)
            question.tags.add(tag)
            tag.used_count += 1
            tag.save()

        questions.append(question)
        print(f"  ✓ Question: {q_data['title'][:60]}...")

    return questions

def create_answers(users, questions):
    """Create answers for questions"""
    print("\n[4/7] Creating answers...")

    answers = []
    answer_count = 0

    for question in questions:
        # Each question gets 1-4 answers
        num_answers = random.randint(1, 4)

        for j in range(num_answers):
            author = random.choice([u for u in users if u != question.author])

            answer_body = random.choice(ANSWERS) + "\n\n```python\n# Sample code here\nprint('Hello, World!')\n```"

            answer = Answer(
                title='',
                body=answer_body,
                tagnames='',
                author=author,
                parent=question,
                added_at=question.added_at + timedelta(hours=random.randint(1, 48)),
                score=random.randint(-2, 20),
                state_string='(published)',
            )
            answer.save()

            answers.append(answer)
            answer_count += 1

        # Update question's extra_count
        question.extra_count = num_answers
        question.save()

    print(f"  ✓ Created {answer_count} answers")
    return answers

def create_comments(users, questions, answers):
    """Create comments on questions and answers"""
    print("\n[5/7] Creating comments...")

    comment_count = 0
    all_nodes = list(questions) + list(answers)

    for node in all_nodes:
        # 50% chance of having comments
        if random.random() < 0.5:
            num_comments = random.randint(1, 3)

            for k in range(num_comments):
                author = random.choice([u for u in users if u != node.author])

                comment = Comment(
                    title='',
                    body=random.choice(COMMENTS),
                    tagnames='',
                    author=author,
                    parent=node,
                    added_at=node.added_at + timedelta(hours=random.randint(1, 72)),
                    score=random.randint(0, 5),
                    state_string='(published)',
                )
                comment.save()

                comment_count += 1

    print(f"  ✓ Created {comment_count} comments")

def create_votes(users, questions, answers):
    """Create votes on questions and answers"""
    print("\n[6/7] Creating votes...")

    vote_count = 0
    all_votable = list(questions) + list(answers)

    for node in all_votable:
        # Random number of votes
        num_votes = random.randint(0, 10)

        voters = random.sample([u for u in users if u != node.author],
                              min(num_votes, len(users) - 1))

        for voter in voters:
            # 80% upvote, 20% downvote
            vote_value = 1 if random.random() < 0.8 else -1

            try:
                Vote.objects.create(
                    user=voter,
                    node=node,
                    value=vote_value,
                    voted_at=node.added_at + timedelta(hours=random.randint(1, 100)),
                )
                vote_count += 1
            except:
                pass  # Skip if vote already exists

    print(f"  ✓ Created {vote_count} votes")

def create_settings():
    """Create some settings"""
    print("\n[7/7] Creating settings...")

    settings_data = [
        ('APP_TITLE', 'OSQA: Open Source Q&A Forum'),
        ('APP_SHORT_NAME', 'OSQA'),
        ('APP_DESCRIPTION', 'Ask and answer questions, share knowledge.'),
        ('WELCOME_MESSAGE', 'Welcome to our Q&A community!'),
    ]

    for key, value in settings_data:
        KeyValue.objects.update_or_create(
            key=key,
            defaults={'value': value}
        )
        print(f"  ✓ Setting: {key}")

def main():
    print("\nStarting database population...\n")

    users = create_users(15)
    tags = create_tags()
    questions = create_questions(users, tags)
    answers = create_answers(users, questions)
    create_comments(users, questions, answers)
    create_votes(users, questions, answers)
    create_settings()

    print("\n" + "="*80)
    print("✓ DUMMY DATA CREATED SUCCESSFULLY!")
    print("="*80)
    print("\nCreated:")
    print(f"  - {len(users)} users (including admin 'alfonso')")
    print(f"  - {len(tags)} tags")
    print(f"  - {len(questions)} questions")
    print(f"  - {len(answers)} answers")
    print("  - Votes and comments on content")
    print("\nAdmin Login:")
    print("  Username: alfonso")
    print("  Password: alfonso")
    print("  URL: http://127.0.0.1:8000/nimda/")
    print("\nHomepage: http://127.0.0.1:8000/")
    print("="*80)

if __name__ == '__main__':
    main()
