// In-process measurements of the C++ version, for work GNOME Files has no equivalent of (see README.md).
// Usage: kestrel_cpp TEST DATA_DIR [ARG]. Prints {"s": seconds, "n": items, "rss_mb": peak memory}.
//
//   trash      move every file in ~/victim to the trash with the window's Move to Trash, then Empty Trash ("empty_s")
//   move_xdev  move ~/movesrc to ARG (on another drive)
#include "app.h"
#include "fileops.h"
#include "metadata.h"
#include "thumbs.h"
#include "util.h"
#include "widgets.h"

#include <QApplication>
#include <QElapsedTimer>
#include <QAbstractButton>
#include <QEventLoop>
#include <QMessageBox>
#include <QTimer>

#include <csignal>
#include <cstdio>
#include <functional>

using namespace util;

static void wait_for(const std::function<bool()> &cond, int timeout_ms = 600000)
{
    QEventLoop loop;
    QTimer t;
    QObject::connect(&t, &QTimer::timeout, [&] {
        if (cond())
            loop.quit();
    });
    t.start(5);
    QTimer::singleShot(timeout_ms, &loop, &QEventLoop::quit);
    loop.exec();
}

static double peak_mb()
{
    for (const QByteArray &line : read_file("/proc/self/status").split('\n'))
        if (line.startsWith("VmHWM:"))
            return line.mid(6).trimmed().split(' ').first().toDouble() / 1024;
    return 0;
}

int main(int argc, char **argv)
{
    std::signal(SIGPIPE, SIG_IGN);
    QString test = argv[1], D = argv[2], ARG = argc > 3 ? QString::fromLocal8Bit(argv[3]) : QString();
    QApplication::setApplicationName(APP_ID);
    QApplication qapp(argc, argv);
    qRegisterMetaType<fileops::DirStats>();
    auto *tm = new ThumbnailManager;
    apply_thumb_settings(tm);
    long n = -1;
    double s = -1, empty_s = -1;
    long empty_left = -1;
    QString dst;
    QElapsedTimer t;
    t.start();
    if (test == "bulk_thumbs" || test == "mosaics") {
        bool fin = false;
        int done = 0;
        auto *b = new RecursiveBuilder(test == "bulk_thumbs" ? D + "/gallery" : D + "/albums", 160, tm);
        QObject::connect(b, &RecursiveBuilder::finished_build, [&](int d, int, bool) {
            fin = true;
            done = d;
        });
        b->start();
        wait_for([&] { return fin; });
        b->wait();
        n = done;
    } else if (test == "search") {
        long found = 0;
        auto *st = new SearchThread(D + "/tree", "*_7.jpg", false);
        QObject::connect(st, &SearchThread::found, [&](const QStringList &b) { found += b.size(); });
        st->start();
        wait_for([&] { return st->isFinished(); });
        qapp.processEvents();
        n = found;
    } else if (test == "copy") {
        bool fin = false;
        dst = join(HOME(), "copydst");
        fileops::start_ops(nullptr, {{"copy", D + "/copysrc", dst}}, "Copying", [&] { fin = true; });
        wait_for([&] { return fin; });
    } else if (test == "trash") {
        auto *w = new MainWindow({HOME()}, tm);
        w->show();
        QString victim = join(HOME(), "victim");
        QStringList paths;
        for (const QString &name : listdir(victim))
            paths << join(victim, name);
        paths.sort();
        t.restart();
        w->trash_paths(paths);
        wait_for([&] { return w->task_panel->tasks.isEmpty(); });
        s = t.elapsed() / 1000.0;
        n = paths.size() - listdir(victim).size();
        qint64 clicked = -1;
        QTimer closer;   // Empty Trash asks first: answer like a user would, and start the clock there
        QObject::connect(&closer, &QTimer::timeout, [&] {
            if (auto *m = qobject_cast<QMessageBox *>(QApplication::activeModalWidget()); m && clicked < 0) {
                clicked = t.elapsed();
                m->button(QMessageBox::Yes)->click();
            }
        });
        closer.start(5);
        w->empty_trash();
        wait_for([&] { return clicked >= 0 && w->task_panel->tasks.isEmpty(); });
        empty_s = (t.elapsed() - clicked) / 1000.0;
        QString trash_files = join(HOME(), ".local/share/Trash/files");
        empty_left = isdir(trash_files) ? listdir(trash_files).size() : 0;
    } else if (test == "move_xdev") {
        bool fin = false;
        fileops::start_ops(nullptr, {{"move", join(HOME(), "movesrc"), ARG}}, "Moving", [&] { fin = true; });
        wait_for([&] { return fin; });
    } else if (test == "metadata") {
        QStringList files = listdir(D + "/meta");
        files.sort();
        for (const QString &f : files) {
            metadata::basic_info(D + "/meta/" + f);
            metadata::ai_info(D + "/meta/" + f);
        }
        for (int i = 0; i < 200; ++i)
            metadata::basic_info(QString("%1/gallery/img%2.jpg").arg(D).arg(i, 4, 10, QChar('0')));
        n = 400;
    }
    if (s < 0)
        s = t.elapsed() / 1000.0;
    if (empty_s >= 0)
        std::printf("{\"s\": %f, \"n\": %ld, \"empty_s\": %f, \"empty_left\": %ld, \"rss_mb\": %f}\n", s, n,
                    empty_s, empty_left, peak_mb());
    else
        std::printf("{\"s\": %f, \"n\": %ld, \"rss_mb\": %f}\n", s, n, peak_mb());
    std::fflush(stdout);
    if (!dst.isEmpty())
        rmtree(dst);
    std::_Exit(0);
}
