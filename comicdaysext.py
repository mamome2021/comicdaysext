#!/usr/bin/env python3
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor, wait
import multiprocessing
from pathlib import Path
import signal
import sys
import traceback

import httpx

class ExtractorNoChapterBase(ABC):

    # Override this for setting extension of downloaded images
    image_extension = None
    pool = None

    @abstractmethod
    def name(self):
        """Website name of extractor, for filename of session file"""
        return ''

    def __init__(self):
        """Create extractor class, read session file"""

        # Override this with ProcessPoolExecutor for multiprocessing
        self.Executor = ThreadPoolExecutor
        self.is_interrupted = False
        self.client = httpx.Client()

        # 讀取登錄信息
        # Use 0 instead of empty string to avoid LocalProtocolError
        self.token = '0'
        try:
            session_file = Path(__file__).parent / (self.name + '-session')
            with open(session_file, 'r') as f:
                self.token = f.read().rstrip()
        except:
            pass

        # 讀取設定
        self.config = {
            'threads': 4,
            'retries': 20
        }
        try:
            if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
                # In PyInstaller bundle
                config_filename = Path(sys.executable).parent / f'{self.name}-config.txt'
            else:
                config_filename = Path(__file__).parent / f'{self.name}-config.txt'
            if config_filename.exists():
                with config_filename.open('r', encoding='utf-8') as config_file:
                    lines = config_file.read().split('\n')
                for line in lines:
                    option = line.split()
                    if len(option) == 0:
                        continue
                    if option[0] == 'threads':
                        self.config['threads'] = int(option[1])
                    elif option[0] == 'retries':
                        self.config['retries'] = int(option[1])
        except Exception:
            print(traceback.format_exc())

    def main(self):
        signal.signal(signal.SIGINT, self.interrupt)
        self.arg_parse()

    def interrupt(self, sig, frame):
        if not multiprocessing.parent_process():
            print('收到中斷訊號，將結束程式')
        self.is_interrupted = True

    def create_help(self, login, bought, search):
        """Create help text

        :param login: Instructions for login. Will not be displayed if it evaluates to False
        :type login: str
        :param bought: Whether to display instructions for bought comics
        :type bought: bool
        :param search: Whether to display instructions for searching comics
        :type search: bool
        """
        text = '用法：\n'
        if login:
            text += f'{sys.argv[0]} login {login}\n'
        if bought:
            text += f'{sys.argv[0]} list-comic\n    列出已購漫畫\n'
        if search:
            text += f'{sys.argv[0]} search QUERY\n    搜索漫畫。QUERY為關鍵字\n'
        text += f'''{sys.argv[0]} dl [-o 下載位置] COMIC_ID ...
    下載漫畫。COMIC_ID為漫畫的ID。可指定多個COMIC_ID
'''
        return text

    @abstractmethod
    def show_help(self):
        """Show help text, can use create_help() to make help text"""
        pass

    def str_to_index(self, string, length):
        """Convert user input string to index of chapter list

        :param string: user input string
        :type string: str
        :param length: length of chapter list
        :type length: int
        :return: List of index
        :rtype: list[int]
        """
        def str_to_int(s, length):
            if s[0] == 'r':
                return length - int(s[1:])
            else:
                return int(s) - 1

        ret = []
        for s in string.split(','):
            if '-' in s:
                start, end = [str_to_int(i, length) for i in s.split('-')]
                if start > end:
                    ret += list(range(start, end - 1, -1))
                else:
                    ret += list(range(start, end + 1))
            else:
                ret.append(str_to_int(s, length))
        return ret

    def get_location(self):
        """Parse sys.argv and determine download location

        :return: Location for download file
        :rtype: str
        """
        try:
            pos = sys.argv.index('-o')
            location = sys.argv[pos + 1]
            del sys.argv[pos:pos + 2]
            return location
        except ValueError:
            return ''
        except IndexError:
            self.show_help()
            sys.exit(0)

    def arg_parse(self):
        """Parse sys.argv and do action"""
        if len(sys.argv) < 2:
            self.show_help()
            sys.exit(0)
        elif sys.argv[1] == 'login':
            if len(sys.argv) < 3:
                self.show_help()
                sys.exit(0)
            self.login(sys.argv[2:])
        elif sys.argv[1] == 'list-comic':
            if len(sys.argv) != 2:
                self.show_help()
                sys.exit(0)
            self.showBoughtComicList()
        elif sys.argv[1] == 'dl':
            location = self.get_location()
            if len(sys.argv) < 3:
                self.show_help()
                sys.exit(0)
            for comic_id in sys.argv[2:]:
                if self.is_interrupted:
                    return
                try:
                    self.downloadComic(comic_id, location)
                except Exception as e:
                    print(traceback.format_exc())
                    print(f'章節 {comic_id} 下載失敗：{e}')
        else:
            self.show_help()

    def decrypt_image(self, encrypted, idx, image_url, decrypt_info):
        """Override this if downloaded images need to be decrypted

        :param encrypted: encrypted image content
        :type encrypted: bytes
        :param idx: index (page number) of image, starts from 1
        :type idx: int
        :param image_url: url of image
        :type image_url: httpx.URL
        :param decrypt_info: Information for image decryption
        :return: decrypted image
        :rtype: bytes
        """
        return encrypted

    def get_request(self, url, headers=None, cookies=None):
        """Wrapper of httpx.get() to retry failed request"""
        for i in range(self.config['retries']):
            if self.is_interrupted:
                raise Exception('被中斷')
            try:
                return self.client.get(url, headers=headers, cookies=cookies)
            except Exception as e:
                if i == self.config['retries'] - 1:
                    raise e

    def post_request(self, url, data=None, json=None, headers=None, cookies=None):
        """Wrapper of httpx.post() to retry failed request"""
        for i in range(self.config['retries']):
            if self.is_interrupted:
                raise Exception('被中斷')
            try:
                return self.client.post(url, data=data, json=json, headers=headers, cookies=cookies)
            except Exception as e:
                if i == self.config['retries'] - 1:
                    raise e

    def send_request(self, request):
        """Wrapper of httpx.Client.send() to retry failed request"""
        for i in range(self.config['retries']):
            if self.is_interrupted:
                raise Exception('被中斷')
            try:
                return self.client.send(request)
            except Exception as e:
                if i == self.config['retries'] - 1:
                    raise e

    def download_img(self, idx, image_request, path, decrypt_info):
        """Called by download_worker to download image

        :param idx: index (page number) of image, starts from 1
        :type idx: int
        :param image_request: request of image
        :type image_request: httpx.Request
        :param path: Download location
        :type path: Path
        :param decrypt_info: Information for image decryption
        """
        try:
            if self.is_interrupted:
                return
            if self.image_extension:
                ext = self.image_extension
            else:
                ext = Path(image_request.url.path).suffix
                # Fix Kuaikan and Kakao extension
                if ext == '.h' or ext == '.cef':
                    ext = Path(Path(image_request.url.path).stem).suffix
                if not ext:
                    ext = '.jpg'
            filename = Path(path, str(idx).zfill(3) + ext)
            existing_index = [file.stem for file in Path(path).iterdir()]
            if str(idx).zfill(3) in existing_index:
                return
            r = self.send_request(image_request)
            # Fix toptoontw
            if ext == '.php' and r.headers['content-type'] == 'image/jpeg':
                filename = Path(path, str(idx).zfill(3) + '.jpg')
            content = self.decrypt_image(r.content, idx, image_request.url, decrypt_info)
            with filename.open('wb') as f:
                f.write(content)
        except Exception as e:
            print(traceback.format_exc())
            print(path / str(idx).zfill(3), '下載失敗：', e)

    def download_list(self, image_download):
        """Download images

        :param image_download: ImageDownload object
        :type image_download: ImageDownload
        """
        if self.is_interrupted:
            return
        root = image_download.root
        comic_title = self.fix_filename(image_download.comic_title)
        if image_download.chapter_title:
            chapter_title = self.fix_filename(image_download.chapter_title)
            print(f'下載{comic_title}/{chapter_title}')
            path = Path(root, comic_title, chapter_title)
        else:
            print(f'下載{comic_title}')
            path = Path(root, comic_title)
        path.mkdir(parents=True, exist_ok=True)
        if not ExtractorBase.pool:
            ExtractorBase.pool = self.Executor(max_workers=self.config['threads'])
        futures = [ExtractorBase.pool.submit(self.download_img, idx + 1, url, path, image_download.decrypt_info) for idx, url in enumerate(image_download.requests)]
        wait(futures)

    def fix_filename(self, name):
        """Convert invalid filename to valid name

        :param name: comic or chapter name, which may be invalid filename
        :type name: str
        :return: valid filename
        :rtype: str
        """
        table = str.maketrans('<>:"/\\|?*', '＜＞：＂⧸⧹│？＊', ''.join(map(chr,range(32))))
        return name.translate(table).rstrip(' .')

    def login(self, token):
        """Write login information (token) to local session file

        :param token: User input token
        :type token: list[str]
        """
        if len(token) == 0:
            self.show_help()
            sys.exit(0)
        session_file = Path(__file__).parent / (self.name + '-session')
        with open(session_file, 'w') as f:
            for i in token:
                f.write(i + '\n')

    @abstractmethod
    def downloadComic(self, comic_id, root):
        """Fetch image list of comic and download

        :param comic_id: id of comic
        :type comic_id: str
        :param root: root directory of download location
        :type root: str
        """
        pass

    def getBoughtComicList(self):
        """Fetch bought comic list from website

        :return: List of comic
        :rtype: list[Comic]
        """
        self.show_help()
        sys.exit(0)

    def showBoughtComicList(self):
        """Display bought comic list"""
        for comic in self.getBoughtComicList():
            print(comic.comic_id, comic.title)

    def draw_image(self, src, dest, sx, sy, width, height, dx, dy):
        """Draw rectangular region of src image to dest image

        :param src: Source image
        :type src: PIL.Image.Image
        :param dest: Destination image
        :type dest: PIL.Image.Image
        :param sx: X coordinate of rectangle of source image
        :type sx: int
        :param sy: Y coordinate of rectangle of source image
        :type sy: int
        :param width: Width of rectangle
        :type width: int
        :param height: Width of rectangle
        :type height: int
        :param dx: X coordinate of rectangle of dest image
        :type dx: int
        :param dy: Y coordinate of rectangle of dest image
        :type dy: int
        """
        crop = src.crop((sx, sy, sx + width, sy + height))
        dest.paste(crop, (dx, dy))

class ExtractorBase(ExtractorNoChapterBase):

    def create_help(self, login, bought, search, removed):
        """Create help text

        :param login: Instructions for login. Will not be displayed if it is None
        :type login: str | None
        :param bought: Whether to display instructions for bought comics
        :type bought: bool
        :param search: Whether to display instructions for searching comics
        :type search: bool
        :param removed: Whether to display instructions for downloading removed comics
        :type removed: bool
        """
        text = '用法：\n'
        if login is not None:
            text += f'{sys.argv[0]} login {login}\n'
        if bought:
            text += f'''{sys.argv[0]} list-comic
    列出已購漫畫
{sys.argv[0]} list-bought-chapter COMIC_ID
    列出已購漫畫章節。COMIC_ID為漫畫的ID
'''
        if search:
            text += f'{sys.argv[0]} search QUERY\n    搜索漫畫。QUERY為關鍵字\n'
        text += f'''{sys.argv[0]} list-chapter COMIC_ID
    列出漫畫章節。COMIC_ID為漫畫的ID
{sys.argv[0]} dl [-o 下載位置] COMIC_ID CHAPTER_ID ...
    下載漫畫。COMIC_ID為漫畫的ID，CHAPTER_ID為章節的ID。可指定多個CHAPTER_ID
{sys.argv[0]} dl-all [-o 下載位置] COMIC_ID ...
    下載漫畫所有章節。COMIC_ID為漫畫的ID。可指定多個COMIC_ID
{sys.argv[0]} dl-seq [-o 下載位置] COMIC_ID ... INDEX
    依照章節序號下載漫畫。COMIC_ID為漫畫的ID，可指定多個COMIC_ID。INDEX為章節在list-bought-chapter中的序號，序號前加r代表反序。可使用-代表範圍，用,下載不連續章節。
'''
        if removed:
            text += f'''{sys.argv[0]} dl-removed [-o 下載位置] COMIC_ID CHAPTER_ID ...
    下載下架漫畫。COMIC_ID為漫畫的ID，CHAPTER_ID為章節的ID。可指定多個CHAPTER_ID
{sys.argv[0]} dl-all-removed [-o 下載位置] COMIC_ID ...
    下載下架漫畫所有章節。COMIC_ID為漫畫的ID。可指定多個COMIC_ID
{sys.argv[0]} dl-seq-removed [-o 下載位置] COMIC_ID ... INDEX
    依照章節序號下載下架漫畫。COMIC_ID為漫畫的ID，可指定多個COMIC_ID。INDEX為章節在list-bought-chapter中的序號，序號前加r代表反序。可使用-代表範圍，用,下載不連續章節。
'''
        return text

    def arg_parse(self):
        """Parse sys.argv and do action"""
        if len(sys.argv) < 2:
            self.show_help()
            sys.exit(0)
        elif sys.argv[1] == 'login':
            if len(sys.argv) < 3:
                self.show_help()
                sys.exit(0)
            self.login(sys.argv[2:])
        elif sys.argv[1] == 'list-comic':
            if len(sys.argv) != 2:
                self.show_help()
                sys.exit(0)
            self.showBoughtComicList()
        elif sys.argv[1] == 'search':
            if len(sys.argv) < 3:
                self.show_help()
                sys.exit(0)
            self.showSearchComicList(sys.argv[2])
        elif sys.argv[1] == 'list-chapter':
            if len(sys.argv) != 3:
                self.show_help()
                sys.exit(0)
            self.showChapterList(sys.argv[2])
        elif sys.argv[1] == 'list-bought-chapter':
            if len(sys.argv) != 3:
                self.show_help()
                sys.exit(0)
            self.showBoughtChapterList(sys.argv[2])
        elif sys.argv[1] == 'dl':
            location = self.get_location()
            if len(sys.argv) < 4:
                self.show_help()
                sys.exit(0)
            for chapter_id in sys.argv[3:]:
                if self.is_interrupted:
                    return
                try:
                    self.downloadChapter(sys.argv[2], chapter_id, location)
                except Exception as e:
                    print(traceback.format_exc())
                    print(f'章節 {chapter_id} 下載失敗：{e}')
        elif sys.argv[1] == 'dl-seq' or sys.argv[1] == 'dl-all':
            if sys.argv[1] == 'dl-all':
                sys.argv.append("1-r1")
            location = self.get_location()
            if len(sys.argv) < 4:
                self.show_help()
                sys.exit(0)
            for comic in sys.argv[2:-1]:
                if self.is_interrupted:
                    return
                try:
                    chapter_list = self.getChapterList(comic)
                except Exception as e:
                    print(f'漫畫 {comic} 無法獲得章節清單：{e}')
                    continue

                for index in self.str_to_index(sys.argv[-1], len(list(chapter_list))):
                    try:
                        chapter_id = str(chapter_list[index].chapter_id)
                    except IndexError:
                        print(f'錯誤：沒有第{index + 1}章')
                        continue
                    if self.is_interrupted:
                        return
                    try:
                        self.downloadChapter(comic, chapter_id, location)
                    except Exception as e:
                        print(traceback.format_exc())
                        print(f'章節 {chapter_id} 下載失敗：{e}')
        elif sys.argv[1] == 'dl-removed':
            location = self.get_location()
            if len(sys.argv) < 4:
                self.show_help()
                sys.exit(0)
            for chapter_id in sys.argv[3:]:
                if self.is_interrupted:
                    return
                try:
                    self.downloadRemovedChapter(sys.argv[2], chapter_id, location)
                except Exception as e:
                    print(traceback.format_exc())
                    print(f'章節 {chapter_id} 下載失敗：{e}')
        elif sys.argv[1] == 'dl-seq-removed' or sys.argv[1] == 'dl-all-removed':
            if sys.argv[1] == 'dl-all-removed':
                sys.argv.append("1-r1")
            location = self.get_location()
            if len(sys.argv) < 4:
                self.show_help()
                sys.exit(0)
            for comic in sys.argv[2:-1]:
                if self.is_interrupted:
                    return
                try:
                    chapter_list = self.getBoughtChapterList(comic)
                except Exception as e:
                    print(f'漫畫 {comic} 無法獲得章節清單：{e}')
                    continue
                for index in self.str_to_index(sys.argv[-1], len(list(chapter_list))):
                    try:
                        chapter_id = str(chapter_list[index].chapter_id)
                    except IndexError:
                        print(f'錯誤：沒有第{index + 1}章')
                        continue
                    if self.is_interrupted:
                        return
                    try:
                        self.downloadRemovedChapter(comic, chapter_id, location)
                    except Exception as e:
                        print(traceback.format_exc())
                        print(f'章節 {chapter_id} 下載失敗：{e}')
        else:
            self.show_help()

    @abstractmethod
    def getChapterList(self, comic_id):
        """Fetch chapter list from website

        :param comic_id: id of comic
        :type comic_id: str
        :return: List of chapter
        :rtype: list[Chapter]
        """
        pass

    def showChapterList(self, comic_id):
        """Display chapter list

        :param comic_id: id of comic
        :type comic_id: str
        """
        for index, chapter in enumerate(self.getChapterList(comic_id)):
            if chapter.locked_status == LockedStatus.locked:
                print('(鎖)', index + 1, chapter.title)
            else:
                print(index + 1, chapter.title)

    @abstractmethod
    def downloadChapter(self, comic_id, chapter_id, root):
        """Fetch image list of chapter and download

        :param comic_id: id of comic
        :type comic_id: str
        :param chapter_id: id of chapter
        :type chapter_id: str
        :param root: root directory of download location
        :type root: str
        """
        pass

    def getBoughtChapterList(self, comic_id):
        """Fetch bought chapter list from website

        :param comic_id: id of comic
        :type comic_id: str
        :return: List of chapter
        :rtype: list[Chapter]
        """
        chapters = self.getChapterList(comic_id)
        ret = []
        for chapter in chapters:
            if chapter.locked_status == LockedStatus.unlocked:
                ret.append(chapter)
        return ret

    def showBoughtChapterList(self, comic_id):
        """Display bought chapter list

        :param comic_id: id of comic
        :type comic_id: str
        """
        for index, chapter in enumerate(self.getBoughtChapterList(comic_id)):
            print(index + 1, chapter.title)

    def downloadRemovedChapter(self, comic_id, chapter_id, root):
        """Fetch image list of chapter of removed comic and download

        :param comic_id: id of comic
        :type comic_id: str
        :param chapter_id: id of chapter
        :type chapter_id: str
        :param root: root directory of download location
        :type root: str
        """
        self.show_help()
        sys.exit(0)

    def searchComic(self, query):
        """Search comic with query

        :param query: search keyword
        :type query: str
        :return: List of comic
        :rtype: list[Comic]
        """
        self.show_help()
        sys.exit(0)

    def showSearchComicList(self, query):
        """Display search result comic list

        :param query: search keyword
        :type query: str
        """
        for comic in self.searchComic(query):
            print(comic.comic_id, comic.title)

    def getTitleIndexFromChapterList(self, comic_id, chapter_id):
        """Get title and index of chapter, by calling getChapterList()

        :param comic_id: id of comic
        :type comic_id: str
        :param chapter_id: id of chapter
        :type chapter_id: str
        :return: title and index of chapter
        :rtype: tuple[str, int]"""
        for index, chapter in enumerate(self.getChapterList(comic_id)):
            if chapter.chapter_id == chapter_id:
                return chapter.title, index

    def downloadComic(self, comic_id, root):
        """Not used"""
        raise Exception('Not used')

class Comic:
    def __init__(self, comic_id, title):
        self.comic_id = comic_id
        self.title = title

class Chapter:
    def __init__(self, chapter_id, title, locked_status):
        self.chapter_id = chapter_id
        self.title = title
        self.locked_status = locked_status

    def __lt__(self, other):
        return self.chapter_id < other.chapter_id

class ImageDownload:
    """Class to pass download information to download_list()

    :param requests: List of image requests to download
    :type requests: list[httpx.Request]
    :param root: root directory of download location
    :type root: str
    :param comic_title: comic title
    :type comic_title: str
    :param chapter_title: chapter title
    :type chapter_title: str
    """

    def __init__(self, root, comic_title, chapter_title=None):
        """Create ImageDownload object

        :param root: root directory of download location
        :type root: str
        :param comic_title: comic title
        :type comic_title: str
        :param chapter_title: chapter title
        :type chapter_title: str
        """
        self.requests = []
        self.root = root
        self.comic_title = comic_title
        self.chapter_title = chapter_title
        self.decrypt_info = None

class LockedStatus:
    locked = 0
    free = 1
    unlocked = 2
    temp_unlocked = 3
import json
import urllib.parse

from bs4 import BeautifulSoup



class Extractor(ExtractorBase):
    name = 'comicdays'

    def __init__(self):
        super().__init__()
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 15; 0; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/138.0.7204.68 Mobile Safari/537.36 ComicDaysAndroid/4.51.0',
            'authorization': f'Bearer {self.token}'
        }

    def show_help(self):
        print(self.create_help('''GLSC
    在網頁版登錄，填入 glsc 這個 cookie''', True, True, False))

    def getChapterList(self, comic_id):
        ret = []
        # Episode
        has_next_page = True
        start = 0
        url = f'https://comic-days.com/api/v1/series/{comic_id}/episodes?sort=number&order=asc'
        while has_next_page:
            if start:
                response = self.get_request(f'{url}&next_to={start}.0', headers=self.headers)
            else:
                response = self.get_request(url, headers=self.headers)
            j = response.json()
            if not 'episodes' in j:
                if 'message' in j:
                    raise Exception(j['message'])
                raise Exception(j['error']['message'])
            for chapter in j['episodes']:
                locked_status = LockedStatus.locked
                if chapter['purchase_info']['is_free']:
                    locked_status = LockedStatus.free
                elif chapter['purchase_info']['has_purchased']:
                    locked_status = LockedStatus.unlocked
                elif chapter['purchase_info']['has_purchased_via_ticket']:
                    locked_status = LockedStatus.temp_unlocked
                ret.append(Chapter(f"episode/{chapter['id']}", chapter['title'], locked_status))
            has_next_page = j['has_next_page']
            start += 50

        # Volume
        has_next_page = True
        start = 0
        url = f'https://comic-days.com/api/v1/series/{comic_id}/volumes?sort=number&order=asc'
        while has_next_page:
            if start:
                response = self.get_request(f'{url}&next_to={start}.0', headers=self.headers)
            else:
                response = self.get_request(url, headers=self.headers)
            j = response.json()
            if not 'volume_info_list' in j:
                if 'message' in j:
                    raise Exception(j['message'])
                raise Exception(j['error']['message'])
            for chapter in [item['volume'] for item in j['volume_info_list']]:
                locked_status = LockedStatus.locked
                if chapter['purchase_info']['is_free']:
                    locked_status = LockedStatus.free
                elif chapter['purchase_info']['has_purchased']:
                    locked_status = LockedStatus.unlocked
                elif chapter['purchase_info']['has_purchased_via_ticket']:
                    locked_status = LockedStatus.temp_unlocked
                ret.append(Chapter(f"volume/{chapter['id']}", chapter['title'], locked_status))
            has_next_page = j['has_next_page']
            start += 50
        return ret

    def downloadChapter(self, comic_id, chapter_id, root):
        headers = {
            'User-Agent': 'Mozilla/5.0 (Linux; Android 15; 0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/135.0.7049.111 Mobile Safari/537.36',
        }
        cookies = {'glsc': self.token}
        response = self.get_request(f'https://comic-days.com/{chapter_id}', headers=headers, cookies=cookies)
        soup = BeautifulSoup(response.text, 'html.parser')
        episode_json = soup.select_one('#episode-json')
        if not episode_json:
            raise Exception(soup.select_one('.error-content-description').text)
        j = json.loads(episode_json.attrs['data-value'])
        comic_title = j['readableProduct']['series']['title']
        chapter_title = j['readableProduct']['title']
        number = str(j['readableProduct']['number']).zfill(3)
        if chapter_id.startswith('volume/'):
            number = 'Vol.' + number
        image_download = ImageDownload(root, comic_title, f'{number} {chapter_title}')
        if not j['readableProduct']['pageStructure']:
            raise Exception('未解鎖')
        for page in j['readableProduct']['pageStructure']['pages']:
            if 'src' in page:
                image_download.requests.append(self.client.build_request('GET', page['src']))
        self.download_list(image_download)

        # Some images from android client are censored, so I use android website instead
        '''response = self.get_request(f'https://comic-days.com/api/v1/{chapter_id}/viewer', headers=self.headers)
        j = response.json()
        if not 'series' in j:
            if 'message' in j:
                raise Exception(j['message'])
            raise Exception(j['error']['message'])
        comic_title = j['series']['title']
        if chapter_id.startswith('episode'):
            chapter_title = j['current_episode']['title']
            number = j['current_episode']['number']
        else:
            chapter_title = j['current_volume']['title']
            number = j['current_volume']['number']
        image_download = ImageDownload(root, comic_title, f'{number:03} {chapter_title}')
        for i in j['image_uris']:
            image_download.requests.append(self.client.build_request('GET', i))
        self.download_list(image_download)'''

    def getBoughtComicList(self):
        response = self.get_request('https://comic-days.com/api/v1/my/purchased?sort=purchased_at', headers=self.headers)
        j = response.json()
        if not 'result' in j:
            raise Exception(j['message'])
        ret = []
        for comic in j['result']:
            ret.append(Comic(comic['series']['id'], comic['series']['title']))
        return ret

    def searchComic(self, query):
        response = self.get_request(f'https://comic-days.com/api/v1/series/search?keyword={urllib.parse.quote_plus(query)}&count_per_page=50&next_to=0', headers=self.headers)
        j = response.json()
        ret = []
        for comic in j['result']:
            ret.append(Comic(comic['series']['id'], comic['series']['title']))
        return ret

if __name__ == '__main__':
    Extractor().main()
