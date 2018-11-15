#!/usr/bin/env python3

import bottle
from bottle import get, post, static_file, request, route, template
from bottle import SimpleTemplate
from configparser import ConfigParser
from ldap3 import Connection, Server, Tls
from ldap3 import SIMPLE, SUBTREE
from ldap3.core.exceptions import LDAPBindError, LDAPConstraintViolationResult, \
    LDAPInvalidCredentialsResult, LDAPUserNameIsMandatoryError, \
    LDAPSocketOpenError, LDAPExceptionError
import logging
import os
from os import environ, path
import ssl

BASE_DIR = path.dirname(__file__)
LOG = logging.getLogger(__name__)
LOG_FORMAT = '%(asctime)s %(levelname)s: %(message)s'
VERSION = '2.0.0'


# tls_conf = Tls(local_private_key_file='ldap.key', local_certificate_file='ldap.pem',
#                validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLSv1, ca_certs_file='ca.pem')
# print(tls_conf)

@get('/')
def get_index():
    return index_tpl()


@post('/')
def post_index():
    form = request.forms.getunicode

    def error(msg):
        return index_tpl(username=form('username'), alerts=[('error', msg)])

    if form('new-password') != form('confirm-password'):
        return error("Password doesn't match the confirmation!")

    if len(form('new-password')) < 8:
        return error("Password must be at least 8 characters long!")

    try:
        change_password(form('username'), form('old-password'), form('new-password'))
    except Error as e:
        LOG.warning("Unsuccessful attempt to change password for %s: %s" % (form('username'), e))
        return error(str(e))

    LOG.info("Password successfully changed for: %s" % form('username'))

    return index_tpl(alerts=[('success', "Password has been changed")])


@route('/static/<filename>', name='static')
def serve_static(filename):
    return static_file(filename, root=path.join(BASE_DIR, 'static'))


def index_tpl(**kwargs):
    return template('index', **kwargs)


def construct_tls():
    try:
        return Tls(local_private_key_file=CONF['tls']['key_file'],
                   local_certificate_file=CONF['tls']['cert_file'],
                   validate=ssl.CERT_REQUIRED, version=ssl.PROTOCOL_TLSv1,
                   ca_certs_file=CONF['tls']['ca_file'])
    except KeyError:
        return None


def connect_ldap(**kwargs):
    tls_conf = construct_tls()
    server = Server(host=CONF['ldap']['host'],
                    port=CONF['ldap'].getint('port', None),
                    use_ssl=CONF['ldap'].getboolean('use_ssl', False),
                    connect_timeout=5,
                    tls=tls_conf)

    return Connection(server, raise_exceptions=True, **kwargs)


def change_password(*args):
    try:
        if CONF['ldap'].get('type') == 'ad':
            change_password_ad(*args)
        else:
            change_password_ldap(*args)

    except (LDAPBindError, LDAPInvalidCredentialsResult, LDAPUserNameIsMandatoryError):
        raise Error('Username or password is incorrect!')

    except LDAPConstraintViolationResult as e:
        # Extract useful part of the error message (for Samba 4 / AD).
        msg = e.message.split('check_password_restrictions: ')[-1].capitalize()
        raise Error(msg)

    except LDAPSocketOpenError as e:
        LOG.error('{}: {!s}'.format(e.__class__.__name__, e))
        raise Error('Unable to connect to the remote server.')

    except LDAPExceptionError as e:
        LOG.error('{}: {!s}'.format(e.__class__.__name__, e))
        raise Error('Encountered an unexpected error while communicating with the remote server.')


def change_password_ldap(username, old_pass, new_pass):
    with connect_ldap() as c:
        user_dn = find_user_dn(c, username, )

    # Note: raises LDAPUserNameIsMandatoryError when user_dn is None.
    with connect_ldap(authentication=SIMPLE, user=user_dn, password=old_pass) as c:
        c.bind()
        c.extend.standard.modify_password(user_dn, old_pass, new_pass)


def change_password_ad(username, old_pass, new_pass):
    user = username + '@' + CONF['ldap']['ad_domain']

    with connect_ldap(authentication=SIMPLE, user=user, password=old_pass) as c:
        c.bind()
        user_dn = find_user_dn(c, username)
        c.extend.microsoft.modify_password(user_dn, new_pass, old_pass)


def find_user_dn(conn, uid):
    search_filter = CONF['ldap']['search_filter'].replace('{uid}', uid)
    conn.search(CONF['ldap']['base'], "(%s)" % search_filter, SUBTREE)
    # print(conn.response[0]['dn'])
    return conn.response[0]['dn'] if conn.response else None


def read_config():
    config = ConfigParser()
    config.read([path.join(BASE_DIR, 'settings.ini'), os.getenv('CONF_FILE', '')])

    return config


class Error(Exception):
    pass


if environ.get('DEBUG'):
    bottle.debug(True)

# Set up logging.
logging.basicConfig(format=LOG_FORMAT)
LOG.setLevel(logging.INFO)
LOG.info("Starting ldap-passwd-webui %s" % VERSION)

CONF = read_config()

bottle.TEMPLATE_PATH = [BASE_DIR]

# Set default attributes to pass into templates.
SimpleTemplate.defaults = dict(CONF['html'])
SimpleTemplate.defaults['url'] = bottle.url

# Run bottle internal server when invoked directly (mainly for development).
if __name__ == '__main__':
    bottle.run(**CONF['server'])
# Run bottle in application mode (in production under uWSGI server).
else:
    application = bottle.default_app()
