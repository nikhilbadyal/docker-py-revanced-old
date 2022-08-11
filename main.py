import subprocess
import sys
from atexit import register
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import PriorityQueue
from shutil import rmtree
from subprocess import PIPE, Popen
from time import perf_counter
from typing import Dict, List, Tuple

from requests import Session
from selectolax.lexbor import LexborHTMLParser
from tqdm import tqdm

temp_folder = Path("apks")
session = Session()
session.headers["User-Agent"] = "anything"
apps = ["youtube", "youtube-music", "twitter", "reddit"]


class Downloader:
    _CHUNK_SIZE = 2**21 * 5
    _QUEUE = PriorityQueue()
    _QUEUE_LENGTH = 0

    @classmethod
    def _download(cls, url: str, file_name: str) -> None:
        print(f"Trying to download {file_name} from apkmirror")
        cls._QUEUE_LENGTH += 1
        start = perf_counter()
        resp = session.get(url, stream=True)
        total = int(resp.headers.get("content-length", 0))
        with temp_folder.joinpath(file_name).open("wb") as dl_file, tqdm(
            desc=file_name,
            total=total,
            unit="iB",
            unit_scale=True,
            unit_divisor=1024,
            colour="green",
        ) as bar:
            for chunk in resp.iter_content(cls._CHUNK_SIZE):
                size = dl_file.write(chunk)
                bar.update(size)
        cls._QUEUE.put((perf_counter() - start, file_name))

    @classmethod
    def apkmirror(cls, app, version: str) -> None:
        print(f"Trying to download {app} apk from apkmirror")
        version = "-".join(
            v.zfill(2 if i else 0) for i, v in enumerate(version.split("."))
        )

        page = """
        https://www.apkmirror.com/apk/google-inc/
        {a}/{a}-{v}-release/{a}-{v}-android-apk-download/
        """
        parser = LexborHTMLParser(session.get(page.format(v=version, a=app)).text)

        resp = session.get(
            "https://www.apkmirror.com"
            + parser.css_first("a.accent_bg").attributes["href"]
        )
        parser = LexborHTMLParser(resp.text)

        href = parser.css_first(
            "p.notes:nth-child(3) > span:nth-child(1) > a:nth-child(1)"
        ).attributes["href"]
        cls._download("https://www.apkmirror.com" + href, "youtube.apk")

    @classmethod
    def repository(cls, name: str) -> None:
        resp = session.get(
            f"https://github.com/revanced/revanced-{name}/releases/latest"
        )
        parser = LexborHTMLParser(resp.text)
        url = parser.css("li.Box-row > div:nth-child(1) > a:nth-child(2)")[:-2][
            -1
        ].attributes["href"]
        cls._download("https://github.com" + url, Path(url).with_stem(name).name)

    @classmethod
    def report(cls) -> None:
        started = False
        while True:
            item = cls._QUEUE.get()
            print(f"{item[1]} downloaded in {item[0]:.2f} seconds.")
            cls._QUEUE.task_done()
            cls._QUEUE_LENGTH -= 1

            if not started:
                started = True
            elif started and not cls._QUEUE_LENGTH:
                break


class Patches:
    def __init__(self):
        resp = session.get(
            "https://raw.githubusercontent.com/revanced/revanced-patches/main/README.md"
        )
        available_patches = []
        for app in resp.text.split("### 📦 ")[1:]:
            lines = app.splitlines()

            app_name = lines[0][1:-1]
            app_patches = []
            for line in lines:
                patch = line.split("|")[1:-1]
                if len(patch) == 3:
                    (n, d, v), a = [i.replace("`", "").strip() for i in patch], app_name
                    app_patches.append((n, d, a, v))

            available_patches.extend(app_patches)

        youtube, music, twitter, reddit = [], [], [], []
        for n, d, a, v in available_patches:
            patch = {"name": n, "description": d, "app": a, "version": v}
            if "twitter" in a:
                twitter.append(patch)
            elif "reddit" in a:
                reddit.append(patch)
            elif "music" in a:
                music.append(patch)
            elif "youtube" in a:
                youtube.append(patch)
        self._yt = youtube
        self._ytm = music
        self._twitter = twitter
        self._reddit = reddit

    def get(self, app) -> Tuple[List[Dict[str, str]], str]:
        print("Getting patches for %s" % app)
        if "twitter" == app:
            patches = self._twitter
        elif "reddit" == app:
            patches = self._reddit
        elif "music" == app:
            patches = self._ytm
        elif "youtube" == app:
            patches = self._yt
        else:
            sys.exit(-1)
        version = next(i["version"] for i in patches if i["version"] != "all")
        print("Version for app is  %s" % version)
        return patches, version


class ArgParser:
    _PATCHES = []

    @classmethod
    def include(cls, name: str) -> None:
        cls._PATCHES.extend(["-i", name])

    @classmethod
    def exclude(cls, name: str) -> None:
        cls._PATCHES.extend(["-e", name])

    @classmethod
    def run(cls, app: str) -> None:
        print(f"Sending request to revanced cli for building {app} revanced")
        args = [
            "-jar",
            "cli.jar",
            "-a",
            app,
            "-b",
            "patches.jar",
            "-m",
            "integrations.apk",
            "-o",
            f"{app}-output.apk",
        ]
        args[1::2] = map(lambda i: temp_folder.joinpath(i), args[1::2])

        if cls._PATCHES:
            args.extend(cls._PATCHES)

        start = perf_counter()
        process = Popen(["java", *args], stdout=PIPE)
        for line in process.stdout:
            print(line.decode(), flush=True, end="")
        process.wait()
        print(
            f"Patching completed for app {app} in {perf_counter() - start:.2f} "
            f"seconds."
        )


@register
def close():
    session.close()
    cache = Path("revanced-cache")
    if cache.is_dir():
        rmtree(cache)


def check_java():
    jd = subprocess.check_output(["java", "-version"], stderr=subprocess.STDOUT)
    jd = str(jd)[1:-1]
    if "Runtime Environment" not in jd:
        print("Java Must be installed")
        exit(-1)


def pre_requisite():
    check_java()
    patches = Patches()
    return patches


def main():
    patches = pre_requisite()
    downloader = Downloader

    with ThreadPoolExecutor() as executor:
        executor.map(downloader.repository, ("cli", "integrations", "patches"))

    def get_patches():
        print(f"Getting patches for app {app}")
        selected_patches = list(range(0, len(app_patches)))
        if app == "youtube":
            selected_patches.remove(9)
        for i, v in enumerate(app_patches):
            arg_parser.include(
                v["name"]
            ) if i in selected_patches else arg_parser.exclude(v["name"])

    for app in apps:
        arg_parser = ArgParser
        print("Trying to build %s" % app)
        app_patches, version = patches.get(app=app)
        with ThreadPoolExecutor() as executor:
            executor.submit(downloader.apkmirror, app, version)
            executor.submit(get_patches).add_done_callback(
                lambda _: downloader.report()
            )
        print("Download completed.")
        arg_parser.run(app=app)
        print("Wait for programme to exit.")


if __name__ == "__main__":
    main()
