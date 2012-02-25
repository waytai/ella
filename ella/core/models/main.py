from django.db import models
from django.core.urlresolvers import reverse
from django.contrib.contenttypes.models import ContentType
from django.contrib.sites.models import Site
from django.utils.translation import ugettext_lazy as _
from django.conf import settings
from django.utils.safestring import mark_safe
from django.contrib.auth.models import User
from django.core.validators import validate_slug


from jsonfield.fields import JSONField

from ella.core.box import Box
from ella.core.cache import get_cached_object, CachedGenericForeignKey
from ella.core.conf import core_settings


class Author(models.Model):
    """
    Describes an Author of the published content. Author can be:
    
    * Human
    * Organization
    * ...
    
    All the fields except for ``slug`` are optional to enable maximum of 
    flexibility.
    """
    user = models.ForeignKey(User, verbose_name=_('User'), blank=True, null=True)
    name = models.CharField(_('Name'), max_length=200, blank=True)
    slug = models.SlugField(_('Slug'), max_length=255, unique=True, validators=[validate_slug])
    description = models.TextField(_('Description'), blank=True)
    text = models.TextField(_('Text'), blank=True)
    email = models.EmailField(_('Email'), blank=True)

    class Meta:
        app_label = 'core'
        verbose_name = _('Author')
        verbose_name_plural = _('Authors')

    def save(self, **kwargs):
        self.full_clean()
        super(Author, self).save(**kwargs)

    def __unicode__(self):
        return self.name


class Source(models.Model):
    """
    A ``Source`` in oposition to ``Author`` is used for publishable content
    that was taken from other sites and it's purpose is mainly for legal matters.
    """
    name = models.CharField(_('Name'), max_length=200)
    url = models.URLField(_('URL'), blank=True)
    description = models.TextField(_('Description'), blank=True)

    class Meta:
        app_label = 'core'
        verbose_name = _('Source')
        verbose_name_plural = _('Sources')

    def __unicode__(self):
        return self.name


class CategoryBox(Box):
    """
    Special Box class for category that adds 'photo_slug' parameter
    to the box's context (if supplied).
    """
    def get_context(self):
        cont = super(CategoryBox, self).get_context()
        if 'photo_slug' in self.params:
            cont['photo_slug'] = self.params['photo_slug']
        return cont

class Category(models.Model):
    """
    ``Category`` is the **basic building block of Ella-based sites**. All the
    published content is divided into categories - every ``Publishable`` object
    has a ``ForeignKey`` to it's primary ``Category``. Primary category is then
    used to build up object's URL when using `Category.get_absolute_url` method. 
    Besides that, objects can be published in other categories (aka "secondary"
    categories) via ``Listing``.

    Every site has exactly one root category (without a parent) that serve's as
    the sites's homepage.
    """
    template_choices = tuple((x, _(y)) for x, y in core_settings.CATEGORY_TEMPLATES)

    title = models.CharField(_("Title"), max_length=200)
    description = models.TextField(_("Description"), blank=True, help_text=_(
        'Description which can be used in link titles, syndication etc.'))
    content = models.TextField(_('Content'), default='', blank=True, help_text=_(
        'Optional content to use when rendering this category.'))
    template = models.CharField(_('Template'), max_length=100, help_text=_(
        'Template to use to render detail page of this category.'),
        choices=template_choices, default=template_choices[0][0])
    slug = models.SlugField(_('Slug'), max_length=255, validators=[validate_slug])
    tree_parent = models.ForeignKey('self', null=True, blank=True,
        verbose_name=_("Parent category"))
    tree_path = models.CharField(verbose_name=_("Path from root category"),
        max_length=255, editable=False)
    site = models.ForeignKey(Site)

    # generic JSON field to store app cpecific data
    app_data = JSONField(_('Custom meta data'), default='{}', blank=True,
        help_text=_('If you need to define custom data for '
        'category objects, you can use this field to do so.'))

    class Meta:
        app_label = 'core'
        unique_together = (('site', 'tree_path'),)
        verbose_name = _('Category')
        verbose_name_plural = _('Categories')

    def __unicode__(self):
        return '%s/%s' % (self.site.name, self.tree_path)

    def save(self, **kwargs):
        "Override save() to construct tree_path based on the category's parent."
        old_tree_path = self.tree_path
        if self.tree_parent:
            if self.tree_parent.tree_path:
                self.tree_path = '%s/%s' % (self.tree_parent.tree_path, self.slug)
            else:
                self.tree_path = self.slug
        else:
            self.tree_path = ''
        self.full_clean()
        super(Category, self).save(**kwargs)
        if old_tree_path != self.tree_path:
            # the tree_path has changed, update children
            children = Category.objects.filter(
                tree_path__startswith=old_tree_path + '/').order_by('tree_path')
            for child in children:
                child.save(force_update=True)

    def get_tree_parent(self):
        """
        Returns parent category, if such exists. If this category doesn't have
        a parent, None is returned.
        
        Result of this method is cached.
        """
        if self.tree_parent_id:
            return get_cached_object(Category, pk=self.tree_parent_id)
        return None

    def get_children(self, recursive=False):
        # TODO: proper caching
        if recursive:
            return list(self.__class__.objects.filter(tree_path__startswith=self.tree_path + '/'))
        return list(self.__class__.objects.filter(tree_parent=self))

    @property
    def main_parent(self):
        """
        Returns parent category, which is considered as **main**. That means
        that the category's parent is the root category.
        
        Result of this method is cached.
        """
        def _get_main_parent(category):
            if not category.get_tree_parent():
                return None
            if not category.get_tree_parent().get_tree_parent():
                return category
            else:
                return _get_main_parent(category.get_tree_parent())
        return _get_main_parent(self)

    @property
    def path(self):
        """
        Returns tree path of the category. Tree path is string that describes
        the whole path from the category root to the position of this category.
        
        @see: Category.tree_path
        """
        if self.tree_parent_id:
            return self.tree_path
        else:
            return self.slug

    def Box(self, box_type, nodelist):
        """
        Returns instance of ``CategoryBox`` -- a custom ``Box`` subclass
        with additional context.
        """
        return CategoryBox(self, box_type, nodelist)

    def get_absolute_url(self):
        """
        Returns absolute URL for the category.
        """
        if not self.tree_parent_id:
            url = reverse('root_homepage')
        else:
            url = reverse('category_detail', kwargs={'category' : self.tree_path})
        if self.site_id != settings.SITE_ID:
            # prepend the domain if it doesn't match current Site
            site = get_cached_object(Site, pk=self.site_id)
            return 'http://' + site.domain + url
        return url

    def draw_title(self):
        """
        Returns title indented by *&nbsp;* elements that can be used to show
        users a category tree.
        
        Examples:
        
        **Category with no direct parent (the category root)**
            TITLE
            
        **Category with one parent**
            &nsbp;TITLE

        **Category on third level of the tree**
            &nbsp;&nbsp;TITLE
        """
        return mark_safe(('&nbsp;&nbsp;' * self.tree_path.count('/')) + self.title)
    draw_title.allow_tags = True


class Dependency(models.Model):
    """
    Captures relations between objects to simplify finding out what other objects
    my object depend on.
    
    This sounds mysterious, but the common use case is quite simple: keeping
    information which objects have been embedded in article content using
    **boxes** for example (these might be photos, galleries, ...).
    """
    target_ct = models.ForeignKey(ContentType, related_name='dependency_for_set')
    target_id = models.IntegerField()
    target = CachedGenericForeignKey('target_ct', 'target_id')

    dependent_ct = models.ForeignKey(ContentType, related_name='depends_on_set')
    dependent_id = models.IntegerField()
    dependent = CachedGenericForeignKey('dependent_ct', 'dependent_id')

    class Meta:
        app_label = 'core'
        verbose_name = _('Dependency')
        verbose_name_plural = _('Dependencies')

    def __unicode__(self):
        return _(u'%s depends on %s') % (self.dependent, self.target)

