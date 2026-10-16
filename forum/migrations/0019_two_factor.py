# Passkeys and two-step verification (forum/models/two_factor.py).

import datetime
import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("forum", "0018_tag_management_actions"),
    ]

    operations = [
        migrations.CreateModel(
            name='Passkey',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=64)),
                ('credential_id', models.TextField()),
                ('credential_hash', models.CharField(max_length=64, unique=True)),
                ('public_key', models.TextField()),
                ('sign_count', models.PositiveBigIntegerField(default=0)),
                ('user_handle', models.CharField(max_length=128)),
                ('transports', models.CharField(blank=True, max_length=200)),
                ('backed_up', models.BooleanField(default=False)),
                ('created', models.DateTimeField(default=datetime.datetime.now)),
                ('last_used', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='passkeys', to='forum.user')),
            ],
            options={
                'ordering': ('created', 'id'),
            },
        ),
        migrations.CreateModel(
            name='RecoveryCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code_hash', models.CharField(max_length=100)),
                ('created', models.DateTimeField(default=datetime.datetime.now)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recovery_codes', to='forum.user')),
            ],
            options={
                'ordering': ('id',),
            },
        ),
        migrations.CreateModel(
            name='TOTPDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('secret', models.TextField()),
                ('created', models.DateTimeField(default=datetime.datetime.now)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('last_step', models.BigIntegerField(default=0)),
                ('last_used', models.DateTimeField(blank=True, null=True)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='totp_device', to='forum.user')),
            ],
        ),
    ]
