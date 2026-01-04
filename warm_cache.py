"""
回测性能优化工具 - 多线程预加载历史数据

使用方法：
1. 先运行此脚本预加载数据：python warm_cache.py
2. 再运行回测脚本：python backtest_doge_1year.py
"""

from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from binance import Client
from sqlitedict import SqliteDict
import threading
import queue
from tqdm import tqdm

# 配置
CACHE_FILE = "data/backtest_cache.db"
START_DATE = datetime(2025, 1, 1, 0, 0, 0)
END_DATE = datetime(2026, 1, 3, 23, 59, 0)

# 需要预加载的币种（所有支持的币种对USDT和BTC）
# 已验证：这些币种都同时有USDT和BTC交易对
COINS = ['ADA', 'ATOM', 'AVAX', 'BAT', 'BNB', 'DASH', 'DOGE', 'DOT',
         'EOS', 'ETC', 'ICX', 'IOTA', 'LINK', 'LTC', 'NEO', 'ONT', 'QTUM',
         'SOL', 'TRX', 'VET', 'XLM']

BRIDGES = ['USDT', 'BTC']

# 异步写入队列
write_queue = queue.Queue(maxsize=1000)  # 限制队列大小，避免内存爆炸
cache_lock = threading.Lock()

def async_writer_worker(cache, stop_event):
    """
    异步写入线程：从队列中取数据批量写入数据库
    """
    batch = []
    batch_size = 5000  # 每5000条数据批量提交一次
    last_commit_time = datetime.now()
    commit_interval = 2  # 每2秒至少提交一次

    while not stop_event.is_set() or not write_queue.empty():
        try:
            # 从队列获取数据（超时1秒）
            item = write_queue.get(timeout=1)

            if item is None:  # 结束信号
                break

            batch.append(item)

            # 批量提交条件：达到batch_size 或 距离上次提交超过commit_interval
            should_commit = (
                len(batch) >= batch_size or
                (datetime.now() - last_commit_time).total_seconds() >= commit_interval
            )

            if should_commit:
                # 批量写入
                with cache_lock:
                    for cache_key, price in batch:
                        cache[cache_key] = price
                    cache.commit()

                batch = []
                last_commit_time = datetime.now()

            write_queue.task_done()

        except queue.Empty:
            # 队列空了，提交剩余数据
            if batch:
                with cache_lock:
                    for cache_key, price in batch:
                        cache[cache_key] = price
                    cache.commit()
                batch = []
                last_commit_time = datetime.now()
            continue

    # 最后提交剩余数据
    if batch:
        with cache_lock:
            for cache_key, price in batch:
                cache[cache_key] = price
            cache.commit()


def download_pair_data(symbol, start_date, end_date, client, cache):
    """
    下载单个交易对的历史数据（异步写入版本）
    """
    max_retries = 3
    retry_count = 0

    while retry_count < max_retries:
        try:
            current_date = start_date
            total_bars = 0
            skipped_bars = 0

            while current_date < end_date:
                # 每次获取1000分钟的数据
                chunk_end = min(current_date + timedelta(minutes=1000), end_date)

                date_str = current_date.strftime("%d %b %Y %H:%M:%S")
                end_str = chunk_end.strftime("%d %b %Y %H:%M:%S")

                # 检查缓存：如果这个chunk的第一条数据已存在，跳过整个chunk
                first_key = f"{symbol} - {date_str}"
                with cache_lock:
                    if first_key in cache:
                        # 已缓存，跳过
                        skipped_bars += min(1000, int((chunk_end - current_date).total_seconds() / 60))
                        current_date = chunk_end
                        continue

                # 调用API下载
                klines = client.get_historical_klines(
                    symbol, "1m", date_str, end_str, limit=1000
                )

                if not klines:
                    break

                # 异步写入：将数据放入队列，不阻塞
                for result in klines:
                    date = datetime.fromtimestamp(result[0] / 1000, tz=timezone.utc).strftime("%d %b %Y %H:%M:%S")
                    price = float(result[1])
                    cache_key = f"{symbol} - {date}"
                    write_queue.put((cache_key, price))  # 放入队列

                total_bars += len(klines)
                current_date = chunk_end

            if skipped_bars > 0:
                return symbol, total_bars, None, skipped_bars
            else:
                return symbol, total_bars, None, 0

        except Exception as e:
            retry_count += 1
            error_msg = str(e)

            # 如果是Invalid symbol错误，不重试
            if 'Invalid symbol' in error_msg or '-1121' in error_msg:
                return symbol, 0, error_msg, 0

            # 网络错误，重试
            if retry_count < max_retries:
                import time
                time.sleep(2 ** retry_count)  # 指数退避：2秒、4秒、8秒
                continue
            else:
                return symbol, 0, f"重试{max_retries}次后失败: {error_msg[:30]}", 0

    return symbol, 0, "未知错误", 0

def warm_cache():
    """
    使用多线程预热缓存
    """
    print("=" * 70)
    print("回测数据预加载工具 - 多线程版本")
    print("=" * 70)
    print(f"\n开始时间: {START_DATE.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"结束时间: {END_DATE.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"时间跨度: {(END_DATE - START_DATE).days} 天")

    # 生成所有需要下载的交易对
    pairs_to_download = []
    for coin in COINS:
        for bridge in BRIDGES:
            pairs_to_download.append(f"{coin}{bridge}")

    # 添加BTCUSDT（用于计算BTC价值）
    pairs_to_download.append("BTCUSDT")

    # 去重
    pairs_to_download = list(set(pairs_to_download))

    print(f"\n需要下载 {len(pairs_to_download)} 个交易对的数据")
    print(f"预计数据量: ~{len(pairs_to_download) * (END_DATE - START_DATE).days * 1440} 条K线")
    print("\n开始多线程下载...")
    print("-" * 70)

    # 初始化
    client = Client()
    cache = SqliteDict(CACHE_FILE, autocommit=False)

    # 启动异步写入线程
    stop_event = threading.Event()
    writer_thread = threading.Thread(
        target=async_writer_worker,
        args=(cache, stop_event),
        daemon=True
    )
    writer_thread.start()
    print("✅ 异步写入线程已启动\n")

    # 多线程下载
    max_workers = 10  # 币安API限制，不要设太高
    success_count = 0
    fail_count = 0
    total_bars = 0
    total_skipped = 0
    total_pairs = len(pairs_to_download)
    completed = 0

    try:
        # 创建进度条
        pbar = tqdm(
            total=total_pairs,
            desc="下载进度",
            unit="对",
            bar_format='{l_bar}{bar}| {n_fmt}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]',
            ncols=100
        )

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            futures = {
                executor.submit(download_pair_data, pair, START_DATE, END_DATE, client, cache): pair
                for pair in pairs_to_download
            }

            # 处理完成的任务
            for future in as_completed(futures):
                pair = futures[future]

                try:
                    symbol, bars, error, skipped = future.result()

                    if error:
                        status = f"❌ {symbol:12s} 失败"
                        fail_count += 1
                        pbar.write(f"  {status}: {error[:50]}")
                    else:
                        total_bars += bars
                        total_skipped += skipped
                        success_count += 1

                        if skipped > 0:
                            status = f"⚡ {symbol:12s} 新增:{bars:6d} 跳过:{skipped:6d}"
                        else:
                            status = f"✅ {symbol:12s} 新增:{bars:6d}条"

                        # 获取队列大小
                        queue_size = write_queue.qsize()
                        pbar.write(f"  {status} | 队列:{queue_size:4d}")

                    # 更新进度条
                    pbar.update(1)
                    pbar.set_postfix({
                        '成功': success_count,
                        '失败': fail_count,
                        '新增': f"{total_bars:,}",
                        '跳过': f"{total_skipped:,}",
                        '队列': write_queue.qsize()
                    })

                except Exception as e:
                    pbar.write(f"  ❌ {pair:12s} 异常: {str(e)[:40]}")
                    fail_count += 1
                    pbar.update(1)

        pbar.close()
        print()

    finally:
        # 等待队列清空
        print("等待数据写入完成...")
        write_queue.join()  # 等待队列中所有任务完成

        # 停止写入线程
        stop_event.set()
        write_queue.put(None)  # 发送结束信号
        writer_thread.join(timeout=10)

        # 关闭缓存
        cache.close()
        print("✅ 数据全部写入完成")

    # 统计
    print("\n" + "=" * 70)
    print("下载完成！")
    print("=" * 70)
    print(f"成功: {success_count} 个交易对")
    print(f"失败: {fail_count} 个交易对")
    print(f"新下载K线数: {total_bars:,} 条")
    print(f"跳过K线数: {total_skipped:,} 条（已缓存）")
    print(f"缓存文件: {CACHE_FILE}")
    print("\n现在可以运行回测脚本了: python backtest_doge_1year.py")
    print("=" * 70)

if __name__ == "__main__":
    warm_cache()
