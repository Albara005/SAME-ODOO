# NOVARA (اسم مؤقت)

منصة موحدة تجمع إدارة الأعمال (على غرار Odoo) مع بناء التطبيقات بالذكاء الاصطناعي، والوكلاء، والأتمتة، والتصميم.
المخطط الكامل في [`docs/PLATFORM_BLUEPRINT.md`](docs/PLATFORM_BLUEPRINT.md).

A unified platform combining an ERP-style business suite with AI app building, agents, automation and design.

## الحالة الحالية

| المرحلة | الحالة | التفاصيل |
|---|---|---|
| 1. الأساس: الحسابات، المؤسسات، الصلاحيات، العزل، التدقيق، النسخ الاحتياطي | ✅ الخلفية مكتملة | [`docs/phase-1-foundation.md`](docs/phase-1-foundation.md) |
| 2. الأعمال: العملاء، المنتجات، الفواتير، دفتر الأستاذ، المخزون، المبيعات | ✅ الخلفية مكتملة | [`docs/phase-2-business.md`](docs/phase-2-business.md) |
| 3. المساعد الذكي: أدوات بصلاحيات المستخدم، موافقة على كل تغيير، ميزانية وتتبع | ✅ الخلفية مكتملة | [`docs/phase-3-assistant.md`](docs/phase-3-assistant.md) |
| 4. AI App Studio: قالب محدد، مُنشئ ذكي، إصدارات وفحوص، معاينة، نشر إنتاج بموافقة | ✅ الخلفية مكتملة | [`docs/phase-4-app-studio.md`](docs/phase-4-app-studio.md) |
| 5. الوكلاء والأتمتة | قيد العمل | |

```
services/api/            FastAPI + SQLAlchemy (Python)
  novara/tenancy.py      عزل المؤسسات التلقائي في طبقة البيانات
  novara/modules/        identity · organizations · access · audit · files
                         partners · catalog · accounting · invoicing · inventory · sales · dataio
  novara/ai/             المساعد الذكي: النماذج، الأدوات، الموافقات، الاستهلاك
  novara/studio/         App Studio: المشاريع، الإصدارات، الفحوص، المُنشئ الذكي، النشر
  migrations/postgres/   المخطط + Row-Level Security + صلاحيات دور التشغيل
  tests/                 اختبارات العزل والصلاحيات (SQLite و PostgreSQL)
infrastructure/
  backup/                نسخ احتياطي مشفّر + التحقق من الاستعادة
  deployment/            أدوار PostgreSQL
docs/                    المخطط ووثائق المراحل
```

## البدء السريع

```bash
cd services/api
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest
uvicorn novara.main:create_app --factory --reload   # http://localhost:8000/docs
```

## قواعد التطوير

- كل ميزة لها اختبارات، وتغييرات قاعدة البيانات عبر `migrations/` فقط.
- كل نموذج يخص مؤسسة يرث `TenantScoped`. لا تكتب شرط `organization_id` يدويًا، ولا تستخدم `system_scope` دون سبب موثّق.
- الصلاحيات تُفرض في الخادم عبر `require(permission)`، وكل عملية حساسة تُسجَّل عبر `audit.record`.
- لا أسرار في Git، والإصدارات مثبتة في `requirements*.txt`.
- لا يُدمج كود مولّد بالذكاء الاصطناعي دون مراجعة واختبارات.
