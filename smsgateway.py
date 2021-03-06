#!/usr/bin/env python2.7
# -*- encoding: utf-8 -*-

import imaplib
import email
import time
import telnetlib
import config
import fcntl
import sys
import datetime

from email.header import decode_header
from messaging.sms import SmsSubmit

instanceSeparator = True

def log(message, level = 'INFO'):
    global instanceSeparator
    if instanceSeparator:
        print ""
        print "=== (INSTANCE) ==="
        instanceSeparator = False
    if config.log:
        message = message.encode('ascii', 'ignore').decode('ascii')
        date = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print ("%s [ %s ] %s" % (date, level, message))

    return True


def csv_config_parser(mailboxes):
    """
    Reads CSV config, returns as a list
    :param mailboxes:
    :return:
    """
    params = []
    with open(mailboxes) as f:
        for line in f:
            if "#" in line:
                pass
            else:
                tmp_str = line.strip("\n").split(",")
                params.append(tmp_str)
    return params


def fetch_unread_mails(mailboxserver, mailboxlogin, mailboxpassword):
    """
    Fetch unread emails on specific mailbox, and returns some fields
    :param mailboxserver:
    :param mailboxlogin:
    :param mailboxpassword:
    :return str:
    """
    mail = imaplib.IMAP4_SSL(mailboxserver)
    mail.login(mailboxlogin, mailboxpassword)
    mail.list()
    mail.select("INBOX")

    mails = []

    n = 0
    returncode, messages = mail.search(None, '(UNSEEN)')
    if returncode == 'OK':
        for num in messages[0].split():
            n += 1
            typ, data = mail.fetch(num, 'RFC822')
            for response_part in data:
                if isinstance(response_part, tuple):
                    original = email.message_from_string(response_part[1])
                    mailfrom = original['From']
                    if "<" in mailfrom:
                        mailfrom = mailfrom.split('<')[1].rstrip('>')
                    mailsubject = original['Subject']
                    mailsubject = decode_header(mailsubject)
                    default_charset = 'ASCII'
                    mailsubject = ''.join([unicode(t[0], t[1] or default_charset) for t in mailsubject])
                    mails.append([mailfrom, mailsubject])
    return mails


def clear_all_sms():
    """
    Clears all stored SMS on Portech like gateways
    :return: None
    """
    try:
        count = 0
        tn = telnetlib.Telnet(config.smshost, 23)
        tn.read_until("username: ")
        tn.write(config.smsusername + "\r\n")
        tn.write(config.smspassword + "\r\n")
        tn.read_until("user level = admin.")
        tn.write("module1\r\n")
        tn.read_until("got!! press 'ctrl-x' to release module 1.")
        while count < 100:
            tn.write("AT+CMGD=" + str(count) + "\r\n")
            count += 1
            tn.read_until("\r\n")
        tn.close()
    except:
        log(("Error clear sms %s" % sys.exc_info()[0]), 'ERROR')
        raise


def resize_ascii_sms(message):
    """
    Strip message if longer than config.smssize
    :param message: Message to resize
    :return message: Resized message
    """
    value = config.smssize-3
    if len(message) > value:
        message = message[:value]
    return message


def resize_pdu_sms(message):
    """
    Strip SMS if longer than config.smssize
    :param message: Message to resize
    :return message: Resized message
    """
    if len(message) > config.smssize:
            message = message[:config.smssize]
    return message


def sms_template(sender, subject):
    """
    Uses a template to make a short message from email fields
    :param sender: str
    :param subject: str
    :return:
    """
    text = config.smstemplate % (sender, subject)
    return text


def imap2sms(conf):
    """
    Send a sms
    :param conf:
    :return:
    """
    for l in conf:
        username = l[1]
        password = l[2]
        mailserver = l[0]
        numbers = []
        i = 3
        while i < len(l):
            numbers.append(l[i])
            i += 1
        mails = fetch_unread_mails(mailserver, username, password)
        for number in numbers:
            for mail in mails:
                sender = mail[0]
                subject = mail[1]
                send_sms(number, subject, sender)


def send_sms(number, subject, sender):
    if config.smsformat == 'pdu':
        sms = resize_pdu_sms(sms_template(sender, subject))
        pdustring, pdulength, phonenumber, message = pdu_format(number, sms)
        send_pdu_sms(pdustring, pdulength)
        log(("Message sent to %s with text %s using PDU method" % (number, message)))
        return True
    elif config.smsformat == 'ascii':
        sms = resize_ascii_sms(sms_template(sender, subject))
        send_ascii_sms(number, sms)
        log(("Message sent to %s with text %s using ASCII method" % (number, sms)))
        return True
    else:
        return False


def pdu_format(phonenumber, message):
    """
    Formats SMS using pdu encoding
    :param phonenumber: Phone number to insert in pdu
    :param message: Text message
    :return: pdustring, pdulenght
    """
    # Whitelist char
    whitelist = ['a', 'b', 'c', 'd', 'e', 'f', 'g', 'h', 'i', 'j', 'k', 'l', 'm', 'n', 'o', 'p', 'q', 'r', 's', 't', 'u', 'v', 'w', 'x', 'y', 'z', 'A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I', 'J', 'K', 'L', 'M', 'N', 'O', 'P', 'Q', 'R', 'S', 'T', 'U', 'V', 'W', 'X', 'Y', 'Z']
    whitelist = whitelist + range(0, 9)
    whitelist = whitelist + [' ', '-', '0', '1', '2', '3', '4', '5', '6', '7', '8', '9']
    fixed_message = ""
    for chr in message:
        if chr in whitelist:
            fixed_message += chr
        else:
            fixed_message += "."

    sms = SmsSubmit(phonenumber, fixed_message)
    pdu = sms.to_pdu()[0]
    pdustring = pdu.pdu
    pdulength = pdu.length
    return pdustring, pdulength, phonenumber, fixed_message


def send_ascii_sms(phonenumber, sms):
    """
    Send SMS using telnetlib, returns exception when issues with telnet communication
    :param phonenumber: Phone number to insert in pdu
    :param sms: Text message
    """
    decoded_sms = sms.encode("ascii", "ignore")
    try:
        time.sleep(2)
        tn = telnetlib.Telnet(config.smshost, 23)
        tn.read_until("username: ")
        tn.write(config.smsusername + "\r\n")
        tn.write(config.smspassword + "\r\n")
        tn.read_until("user level = admin.")
        tn.write("state1\r\n")
        tn.read_until("module 1: free.\r\n]")
        tn.write("module1\r\n")
        tn.read_until("got!! press 'ctrl-x' to release module 1.")
        tn.write("AT+CMGF=1\r\n")
        tn.read_until("0\r\n")
        tn.write('AT+CMGS=%s\r\n' % phonenumber)
        tn.read_until("> ")
        tn.write("%s\x1A" % decoded_sms)
        tn.read_until("+CMGS")
        tn.close()
    except:
        log(("Error when send SMS with Telnet: %s" % sys.exc_info()[0]), 'ERROR')
        raise


def send_pdu_sms(pdustring, pdulength):
    """
    Send SMS using telnetlib, returns exception when issues with telnet communication
    :param pdustring: is the converted sms to pdu format
    :param pdulength: is the size of the pdustring
    """
    try:
        time.sleep(2)
        tn = telnetlib.Telnet(config.smshost, 23)
        tn.read_until("username: ")
        tn.write(config.smsusername + "\r\n")
        tn.write(config.smspassword + "\r\n")
        tn.read_until("user level = admin.")
        tn.write("state1\r\n")
        tn.read_until("module 1: free.\r\n]")
        tn.write("module1\r\n")
        tn.read_until("got!! press 'ctrl-x' to release module 1.")
        tn.write("AT+CMGF=0\r\n")
        tn.read_until("0\r\n")
        tn.write('AT+CMGS=%s\r\n' % pdulength)
        tn.read_until("> ")
        tn.write("%s\r\n\x1A" % pdustring)
        tn.read_until("+CMGS")
        tn.close()
    except:
        log(("Error when send SMS with Telnet: %s" % sys.exc_info()[0]), 'ERROR')
        raise


def usage():
    """
    Prints usage
    :return: str
    """
    usagetext = "smsgateway.py subcommands : \n\n%s imap2sms\n%s sms <number> <message>\n%s clearallsms\n" % (
        sys.argv[0], sys.argv[0], sys.argv[0])
    return usagetext


def debug():
    """
    Debug Function
    :return: bool
    """
    return True


fh = open(config.pidfile, 'w')
try:
    fcntl.lockf(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
except IOError:
    # another instance is running
    log("Error: Another instance is running...", 'ERROR')
    sys.exit(0)

if len(sys.argv) > 1:
    if sys.argv[1] == "clearallsms":
        clear_all_sms()

    elif sys.argv[1] == "sms":
        if len(sys.argv) == 4:
            phonenumber = sys.argv[2]
            sms = sys.argv[3]
            if config.smsformat == "pdu":
                pdustring, pdulength, phonenumber, message = pdu_format(phonenumber, sms)
                send_pdu_sms(pdustring, pdulength)
            elif config.smsformat == "ascii":
                send_ascii_sms(phonenumber, sms)
        else:
            print(usage())

    elif sys.argv[1] == "imap2sms":
        config_params = csv_config_parser(config.mailboxes)
        imap2sms(config_params)

    elif sys.argv[1] == "debug":
        print debug()

    else:
        print(usage())
        exit(1)
else:
    print(usage())
    exit(1)
