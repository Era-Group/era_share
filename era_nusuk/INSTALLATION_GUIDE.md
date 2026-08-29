# دليل تثبيت مديول إدارة العمرة

## المتطلبات الأساسية

### متطلبات النظام
- **نظام التشغيل**: Ubuntu 20.04+ أو CentOS 8+ أو Windows 10+
- **Python**: الإصدار 3.8 أو أحدث
- **PostgreSQL**: الإصدار 12 أو أحدث
- **أودو**: الإصدار 17.0 أو أحدث
- **الذاكرة**: 4 جيجابايت RAM كحد أدنى (8 جيجابايت مُوصى به)
- **مساحة القرص**: 10 جيجابايت مساحة فارغة كحد أدنى

### المتطلبات البرمجية
```bash
# Python packages
pip install psycopg2-binary
pip install Pillow
pip install reportlab
pip install qrcode
```

## خطوات التثبيت التفصيلية

### 1. تحضير البيئة

#### على Ubuntu/Debian:
```bash
# تحديث النظام
sudo apt update && sudo apt upgrade -y

# تثبيت المتطلبات الأساسية
sudo apt install -y python3 python3-pip python3-dev python3-venv
sudo apt install -y postgresql postgresql-contrib
sudo apt install -y git curl wget

# تثبيت wkhtmltopdf للتقارير PDF
sudo apt install -y wkhtmltopdf
```

#### على CentOS/RHEL:
```bash
# تحديث النظام
sudo yum update -y

# تثبيت المتطلبات الأساسية
sudo yum install -y python3 python3-pip python3-devel
sudo yum install -y postgresql postgresql-server postgresql-contrib
sudo yum install -y git curl wget

# تهيئة PostgreSQL
sudo postgresql-setup initdb
sudo systemctl enable postgresql
sudo systemctl start postgresql
```

### 2. إعداد قاعدة البيانات

```bash
# الدخول إلى PostgreSQL
sudo -u postgres psql

# إنشاء مستخدم أودو
CREATE USER odoo WITH CREATEDB PASSWORD 'odoo_password';

# إنشاء قاعدة بيانات للعمرة
CREATE DATABASE umrah_db OWNER odoo;

# الخروج من PostgreSQL
\q
```

### 3. تثبيت أودو

#### الطريقة الأولى: من المصدر (مُوصى بها للتطوير)
```bash
# إنشاء مستخدم أودو
sudo adduser --system --home=/opt/odoo --group odoo

# تحميل أودو
sudo git clone https://www.github.com/odoo/odoo --depth 1 --branch 17.0 /opt/odoo/odoo

# إنشاء بيئة افتراضية
sudo python3 -m venv /opt/odoo/venv

# تفعيل البيئة الافتراضية
sudo -u odoo /opt/odoo/venv/bin/pip install --upgrade pip

# تثبيت متطلبات أودو
sudo -u odoo /opt/odoo/venv/bin/pip install -r /opt/odoo/odoo/requirements.txt
```

#### الطريقة الثانية: من الحزم الرسمية
```bash
# إضافة مستودع أودو
wget -O - https://nightly.odoo.com/odoo.key | sudo apt-key add -
echo "deb http://nightly.odoo.com/17.0/nightly/deb/ ./" | sudo tee /etc/apt/sources.list.d/odoo.list

# تحديث قائمة الحزم وتثبيت أودو
sudo apt update
sudo apt install odoo
```

### 4. تثبيت مديول العمرة

```bash
# إنشاء مجلد للمديولات المخصصة
sudo mkdir -p /opt/odoo/custom-addons

# نسخ مديول العمرة
sudo cp -r /path/to/umrah_management /opt/odoo/custom-addons/

# تعيين الصلاحيات
sudo chown -R odoo:odoo /opt/odoo/custom-addons
```

### 5. إعداد ملف التكوين

```bash
# إنشاء ملف التكوين
sudo nano /etc/odoo/odoo.conf
```

محتوى ملف التكوين:
```ini
[options]
; This is the password that allows database operations:
admin_passwd = admin_password_here
db_host = localhost
db_port = 5432
db_user = odoo
db_password = odoo_password
addons_path = /opt/odoo/odoo/addons,/opt/odoo/custom-addons
logfile = /var/log/odoo/odoo.log
log_level = info

; Security
list_db = False
proxy_mode = True

; Performance
workers = 4
max_cron_threads = 2
limit_memory_hard = 2684354560
limit_memory_soft = 2147483648
limit_request = 8192
limit_time_cpu = 600
limit_time_real = 1200

; Internationalization
without_demo = True
```

### 6. إنشاء خدمة النظام

```bash
# إنشاء ملف الخدمة
sudo nano /etc/systemd/system/odoo.service
```

محتوى ملف الخدمة:
```ini
[Unit]
Description=Odoo
Documentation=http://www.odoo.com
Requires=postgresql.service
After=postgresql.service

[Service]
Type=notify
User=odoo
ExecStart=/opt/odoo/venv/bin/python /opt/odoo/odoo/odoo-bin -c /etc/odoo/odoo.conf
ExecReload=/bin/kill -s HUP $MAINPID
KillMode=mixed

[Install]
WantedBy=multi-user.target
```

### 7. تشغيل الخدمة

```bash
# إعادة تحميل خدمات النظام
sudo systemctl daemon-reload

# تفعيل خدمة أودو
sudo systemctl enable odoo

# تشغيل خدمة أودو
sudo systemctl start odoo

# التحقق من حالة الخدمة
sudo systemctl status odoo
```

### 8. إعداد Nginx (اختياري)

```bash
# تثبيت Nginx
sudo apt install nginx

# إنشاء ملف التكوين
sudo nano /etc/nginx/sites-available/odoo
```

محتوى ملف Nginx:
```nginx
upstream odoo {
    server 127.0.0.1:8069;
}

upstream odoochat {
    server 127.0.0.1:8072;
}

server {
    listen 80;
    server_name your-domain.com;

    proxy_read_timeout 720s;
    proxy_connect_timeout 720s;
    proxy_send_timeout 720s;

    # Add Headers for odoo proxy mode
    proxy_set_header X-Forwarded-Host $host;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_set_header X-Real-IP $remote_addr;

    # log
    access_log /var/log/nginx/odoo.access.log;
    error_log /var/log/nginx/odoo.error.log;

    # Redirect requests to odoo backend server
    location / {
        proxy_redirect off;
        proxy_pass http://odoo;
    }

    # common gzip
    gzip_types text/css text/scss text/plain text/xml application/xml application/json application/javascript;
    gzip on;
}
```

```bash
# تفعيل الموقع
sudo ln -s /etc/nginx/sites-available/odoo /etc/nginx/sites-enabled/

# إعادة تشغيل Nginx
sudo systemctl restart nginx
```

## إعداد المديول في أودو

### 1. الوصول إلى أودو
- افتح المتصفح وانتقل إلى `http://localhost:8069` أو `http://your-domain.com`
- أنشئ قاعدة بيانات جديدة باسم "umrah_db"

### 2. تثبيت المديول
1. انتقل إلى **التطبيقات** (Apps)
2. انقر على **تحديث قائمة التطبيقات** (Update Apps List)
3. ابحث عن "Umrah Management"
4. انقر على **تثبيت** (Install)

### 3. إعداد المستخدمين والصلاحيات
1. انتقل إلى **الإعدادات** > **المستخدمون والشركات** > **المستخدمون**
2. أنشئ مستخدمين جدد أو عدل المستخدمين الموجودين
3. قم بتعيين المجموعات المناسبة:
   - **Umrah Manager**: للمديرين
   - **Umrah User**: للموظفين العاديين
   - **Umrah Agent**: للوكلاء

### 4. الإعداد الأولي للبيانات
1. **إعداد الوكلاء**: انتقل إلى إدارة العمرة > الوكلاء وأضف الوكلاء
2. **إعداد الفنادق**: أضف الفنادق وأنواع الغرف
3. **إعداد البيانات الأساسية**: أضف البلدان والعملات إذا لزم الأمر

## استكشاف الأخطاء وإصلاحها

### مشاكل شائعة وحلولها

#### 1. خطأ في الاتصال بقاعدة البيانات
```bash
# التحقق من حالة PostgreSQL
sudo systemctl status postgresql

# إعادة تشغيل PostgreSQL
sudo systemctl restart postgresql

# التحقق من صحة بيانات الاتصال في ملف التكوين
sudo nano /etc/odoo/odoo.conf
```

#### 2. المديول لا يظهر في قائمة التطبيقات
```bash
# التحقق من مسار المديولات في ملف التكوين
grep addons_path /etc/odoo/odoo.conf

# التحقق من صلاحيات المجلد
ls -la /opt/odoo/custom-addons/umrah_management

# إعادة تشغيل أودو
sudo systemctl restart odoo
```

#### 3. خطأ في الصلاحيات
```bash
# التحقق من ملف الصلاحيات
cat /opt/odoo/custom-addons/umrah_management/security/ir.model.access.csv

# تحديث المديول من واجهة أودو
# انتقل إلى التطبيقات > ابحث عن Umrah Management > انقر على تحديث
```

#### 4. مشاكل في الأداء
```bash
# زيادة عدد العمليات في ملف التكوين
sudo nano /etc/odoo/odoo.conf
# زيادة قيمة workers إلى 6 أو 8

# زيادة الذاكرة المخصصة
# زيادة قيم limit_memory_hard و limit_memory_soft
```

### سجلات النظام

```bash
# عرض سجلات أودو
sudo tail -f /var/log/odoo/odoo.log

# عرض سجلات النظام
sudo journalctl -u odoo -f

# عرض سجلات PostgreSQL
sudo tail -f /var/log/postgresql/postgresql-*.log
```

## النسخ الاحتياطي والاستعادة

### إنشاء نسخة احتياطية
```bash
# نسخة احتياطية من قاعدة البيانات
pg_dump -h localhost -U odoo -d umrah_db > umrah_backup_$(date +%Y%m%d).sql

# نسخة احتياطية من الملفات
tar -czf umrah_files_backup_$(date +%Y%m%d).tar.gz /opt/odoo/custom-addons/umrah_management
```

### استعادة النسخة الاحتياطية
```bash
# استعادة قاعدة البيانات
createdb -h localhost -U odoo umrah_db_restored
psql -h localhost -U odoo -d umrah_db_restored < umrah_backup_20231201.sql

# استعادة الملفات
tar -xzf umrah_files_backup_20231201.tar.gz -C /
```

## الصيانة الدورية

### مهام يومية
- مراقبة سجلات النظام
- التحقق من مساحة القرص
- مراقبة الأداء

### مهام أسبوعية
- إنشاء نسخة احتياطية
- تنظيف سجلات النظام القديمة
- تحديث النظام

### مهام شهرية
- تحليل الأداء
- مراجعة الأمان
- تحديث أودو والمديولات

## الدعم الفني

للحصول على الدعم الفني:
1. راجع هذا الدليل أولاً
2. تحقق من سجلات النظام
3. ابحث في الوثائق الرسمية لأودو
4. تواصل مع فريق الدعم

---

**ملاحظة مهمة**: تأكد من إجراء نسخة احتياطية قبل أي تحديث أو تعديل على النظام.

