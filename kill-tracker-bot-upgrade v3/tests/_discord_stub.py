"""Tiny stand-in for discord.Embed so embed-building logic can be tested without discord.py."""


class Embed:
    def __init__(self, title=None, description=None, color=None, timestamp=None):
        self.title, self.description, self.color, self.timestamp = title, description, color, timestamp
        self.fields, self.footer, self.image = [], None, None
        self.author, self.thumbnail = None, None

    def add_field(self, *, name, value, inline=True):
        self.fields.append((name, value, inline))
        return self

    def set_footer(self, *, text, icon_url=None):
        self.footer = text
        return self

    def set_image(self, *, url):
        self.image = url
        return self

    def set_author(self, *, name, icon_url=None, url=None):
        self.author = name
        return self

    def set_thumbnail(self, *, url):
        self.thumbnail = url
        return self
