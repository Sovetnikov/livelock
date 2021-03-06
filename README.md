# Сервер для координации доступа к ресурсам

От аналогичных механизмов управления доступом к ресурсам основанных на Hazelcast, Consul, ZooKeeper, RedLock и других реализаций на Redis, его отличает лишь возможность забыть об управлении TTL или любыми другими таймаутами при захвате ресурса.

## Управление TTL "как обычно"
Как вы обычно устанавливаете TTL для блокировки ресурса? Примерно оцениваете сколько вам надо времени чтобы успеть закончить работу? Ведёте ли вы статистику того, что вы уклаываетесь в свой TTL? Продлеваете ли TTL если не успеваете? Сколько вы закладываете запаса по TTL, 2x, 3x, 10x?

Сколько времени ресурс будет считаться заблокированным если клиент с десятикратным запасом по TTL нештатно завершит свою работу? Как вы узнаете о том какие ресурсы на самом деле свободны?

Для простых систем всё это не большая проблема, можно всё остановить, сбросить все блокировки и продолжить работать. Но когда количество ваших задач пойдет на сотни в минуту, их продолжительность возрастёт, а ваша система станет критично важной, то у вас появятся проблемы.

Не простая задача управлять TTL правильно.

## Что LiveLock даёт
Сервер LiveLock позволяет забыть о TTL и быть уверенным что ресурсы освобождаются вовремя.

Если LiveLock сообщит вам, что ресурс занят, то вы можете быть уверены что он занят, что его не забыли отпустить, что захвативший процесс не завершился аварийно.

Отслеживание аварийного завершения клиентов основано на поддержании TCP соединения между клиентом и сервером. В случае аварийного завершения процесса клиента операционная система закрывает все его открытые соединения, о чём оповещается сервер LiveLock и освобождает занятые клиентом ресурсы.

## Дополнительные возможности LiveLock
* LiveLock позволяет отслеживать работу, собирать статистику работы временных процессов
* обмениваться сигналами с клиентами захватившими ресурсы (например отправить сигнал с запросом на отмену, если родительская задача уже завершилась)

## Быстрый старт
### Запуск из докера
Пример Dockerfile в репозитории.

Пример для docker-compose:
```yaml
  livelock:
    build:
      context: livelock
    container_name: livelock
    restart: always
    environment:
      SENTRY_DSN: 'https://dsn'
      LIVELOCK_PASSWORD: 'password'
```
### Клиент python
```python
import os
import time

from livelock.client import LiveLock

# Есть интеграция с Django, можно задать эти настройки в settings.py
os.environ['LIVELOCK_HOST'] = 'livelock.server.com'  # По умолчанию 127.0.0.1
os.environ['LIVELOCK_PASSWORD'] = 'password'  # По умолчанию None

with LiveLock('resource_id') as lock:
    time.sleep(10)
```
### Поддерживаемые версии python
Сервер написан на Python 3.7 asyncio.

Клиент поддерживает Python 3.4+

## Ограничения
### LiveLock не оптимизирован для
(такие варианты использования никогда не были целью его создания и не тестировались) 
* быстрых блокировок длительностью менее сотни миллисекунд (вероятно накладные расходы для таких блокировок будут слишком велики)
* работы при неустойчивых сетевых соединениях (исчезновение связи между клиентом и сервером может привести к запаздываниям в закрытии TCP сокета на стороне сервера)
* LiveLock не решает проблемы single point of failure (SPOF)

 <sub><sup>Подробное исследование CloudFlare о жизни TCP сокетов <https://blog.cloudflare.com/when-tcp-sockets-refuse-to-die/>.</sup></sub>
 
### Другие ограничения текущей версии
* все данные хранятся в памяти
* перезапуск сервера приводит к освобождению всех ресурсов (есть механизм дампа состояния сервера на диск и загрузки его при запуске, этот функционал реализован но в стадии отладки)

### Работа в случае обрыва связи до сервера
Протокол сервера LiveLock поддерживает возможность восстановить блокировки клиентом в случае временного нарушения связи.
Такая возможность есть как в случае когда только клиент знает о разрыве соединения, а сервер ещё считает соединение активным, так и в случае когда клиент и сервер знают о рызрыве соединения.

## Производительность

На Core i5-2300 получилось достичь 6000 операций захвата + освобождения ресурсов в секунду (сервер на localhost). 

## Экспорт метрик

Возможен экспорт метрик prometheus если задать порт в переменной окружения LIVELOCK_PROMETHEUS_PORT. Экспортируемые метрики смотри в stats.py.

## Описание протокола