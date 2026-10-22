# المرحلة الأولى: الأساس (Phase 1 — Foundation)

هذا ما بُني فعلًا في `services/api`، وكيف يحقق معيار القبول:
**إنشاء مؤسسة، إضافة مستخدمين، تقييد الوصول لبيانات كل مؤسسة، والتحقق باختبارات.**

## ما تم إنجازه

| البند | التنفيذ | أين |
|---|---|---|
| الحسابات | تسجيل، دخول، خروج، `/me`، قائمة الجلسات والأجهزة وإلغاؤها | `modules/identity` |
| كلمات المرور | Argon2id، و10 أحرف كحد أدنى، ونفس الرد للبريد غير الموجود ولكلمة المرور الخاطئة | `security.py` |
| الجلسات | توكن JWT موقّع يشير إلى صف `user_sessions`، فيمكن إبطاله فورًا | `deps.py` |
| المؤسسات | إنشاء مؤسسة (المنشئ يصبح Owner)، قائمة مؤسساتي، تعديل الإعدادات، و`hosting_mode` | `modules/organizations` |
| العضويات | إضافة، تغيير دور، إيقاف، حذف | `modules/organizations` |
| الأدوار والصلاحيات | كتالوج صلاحيات، أدوار نظام (owner/admin/member/viewer)، أدوار مخصصة لكل مؤسسة | `modules/access` |
| منع التصعيد | لا أحد يمنح أو يسحب صلاحية لا يملكها، ولا يمكن إزالة آخر Owner، وأدوار النظام للقراءة فقط | `organizations/router.py`, `access/router.py` |
| العزل | فلترة تلقائية لكل استعلام + RLS في PostgreSQL | `tenancy.py`, `migrations/postgres/0002` |
| سجل التدقيق | يُكتب في نفس المعاملة مع التغيير، وللإضافة فقط في ORM وعلى مستوى صلاحيات القاعدة | `modules/audit`, `0003` |
| الملفات | رفع وتنزيل وحذف، وحد للحجم، وSHA-256، ومسارات مفصولة لكل مؤسسة | `modules/files` |
| الترحيلات | ملفات SQL مرتبة مع جدول `schema_migrations` وقفل | `novara/migrate.py` |
| النسخ الاحتياطي | `pg_dump` مشفّر بـ age افتراضيًا، وسكربت يثبت الاستعادة فعليًا | `infrastructure/backup` |
| CI | الاختبارات على SQLite ثم على PostgreSQL مع RLS | `.github/workflows/ci.yml` |

## كيف يعمل العزل (طبقتان مستقلتان)

**الطبقة 1: التطبيق (`novara/tenancy.py`).** كل نموذج يرث `TenantScoped` يملك `organization_id`.
بعد `bind_tenant(session, org_id)`، الذي يستدعيه `org_context` تلقائيًا بعد التحقق من العضوية:

- كل `SELECT/UPDATE/DELETE`، بما فيها `session.get()` والتحديث الجماعي، يُفلتر تلقائيًا بالمؤسسة.
- السجلات الجديدة تأخذ `organization_id` تلقائيًا، وأي محاولة لكتابة سجل لمؤسسة أخرى أو نقله إليها تُرفض.
- الاستعلام دون مؤسسة محددة **يفشل بخطأ** بدل أن يعيد بيانات كل المؤسسات.
- الوصول المقصود عبر المؤسسات يتطلب `system_scope` صريحًا، فيسهل حصره ومراجعته (يوجد حاليًا في "قائمة مؤسساتي" فقط).

**الطبقة 2: قاعدة البيانات (PostgreSQL RLS).** الـ API يتصل بدور `novara_app` الذي لا يملك الجداول وليس له `BYPASSRLS`.
في بداية كل معاملة تُضبط `app.current_org` و`app.current_user`، والسياسات تمنع رؤية أو كتابة صفوف مؤسسة أخرى
حتى لو تجاوز كود ما فلتر ORM أو نفّذ SQL خامًا. سجل التدقيق لا يملك هذا الدور عليه `UPDATE` ولا `DELETE`.

الاختبارات تثبت الطبقتين. وتعطيل فلتر ORM مؤقتًا يُفشل 7 اختبارات، أي أنها تقيس العزل فعلًا ولا تنجح بالصدفة.

## التشغيل محليًا

```bash
cd services/api
python -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest                          # SQLite

# PostgreSQL + RLS (بعد تنفيذ infrastructure/deployment/postgres-roles.sql)
export NOVARA_TEST_PG_OWNER_URL=postgresql+psycopg://novara_owner:...@localhost/novara_test
export NOVARA_TEST_PG_APP_URL=postgresql+psycopg://novara_app:...@localhost/novara_test
pytest

# تشغيل الخادم
python -m novara.migrate --database-url postgresql+psycopg://novara_owner:...@localhost/novara --app-role novara_app
export NOVARA_DATABASE_URL=postgresql+psycopg://novara_app:...@localhost/novara NOVARA_JWT_SECRET=...
uvicorn "novara.main:create_app" --factory --reload
# التوثيق التفاعلي: http://localhost:8000/docs
```

## ما لم يُنجز بعد في هذه المرحلة

- **الواجهة الأولية ولوحة الإدارة الداخلية** (React/TypeScript): لم تبدأ. الـ API جاهز لها.
- **دعوة شخص ليس لديه حساب:** الإضافة حاليًا لمستخدم مسجّل فقط، والدعوات بالبريد تحتاج وحدة الإشعارات.
- **التحقق من البريد، والمصادقة متعددة العوامل، وتحديد معدل محاولات الدخول:** الحقول موجودة والتنفيذ لاحقًا. تحديد المعدل مطلوب قبل أي إطلاق عام.
- **تخزين S3 فعلي:** الواجهة `ObjectStorage` جاهزة، والمطبّق حاليًا التخزين المحلي فقط.
- **مسار التشفير بـ age في النسخ الاحتياطي** مكتوب لكنه لم يُختبر هنا لعدم توفر `age`. المسار غير المشفّر والاستعادة اختُبرا على PostgreSQL 16.
- **مفاتيح منع التكرار (idempotency):** تُضاف مع أول عمليات مالية في المرحلة الثانية.
- **مراجعة أمنية خارجية** قبل استخدام بيانات حقيقية.
