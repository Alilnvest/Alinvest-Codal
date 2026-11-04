# Alilnvest Channels

نام عمومی کانال‌های اصلی پروژه در هر دو پیام‌رسان **Alilnvest** است.

## Bale

```env
ALILNVEST_CHANNEL_NAME=Alilnvest
BALE_BOT_TOKEN=توکن_خصوصی_ربات
BALE_CHANNEL_ID=@شناسه_کانال_بله
```

ربات باید در کانال ادمین باشد و مجوز ارسال متن، فایل و تصویر داشته باشد. `BALE_BOT_TOKEN` را در GitHub، README یا پیام عمومی قرار نده.

## Telegram

```env
ALILNVEST_CHANNEL_NAME=Alilnvest
TELEGRAM_BOT_TOKEN=توکن_خصوصی_ربات
TELEGRAM_CHANNEL_ID=@شناسه_کانال_تلگرام
```

ربات تلگرام نیز باید در کانال ادمین باشد. این کتابخانه transport پیام‌رسان را تحمیل نمی‌کند؛ adapter ارسال باید در برنامهٔ اصلی نگهداری شود تا کتابخانه مستقل و قابل استفاده در پروژه‌های مختلف باقی بماند.

## قالب پیشنهادی پیام

```text
📊 Alilnvest | گزارش کدال
نماد: فولاد
دوره: 1405/06/31
منبع: Alilnvest-Codal / Codal
وضعیت داده: معتبر

این گزارش ابزار کمکی تحلیل است و توصیهٔ قطعی سرمایه‌گذاری نیست.
```

## واترمارک و نام فایل

```text
Alilnvest Market Intelligence
Alilnvest Codal Report
Alilnvest-Codal v1.5.3
```

از درج token، مسیر cache خصوصی، شناسه‌های مدیریتی یا اطلاعات حساس در تصویر و کپشن خودداری کن.
