ripple-id
---------

This is a simple webservice that exposes one functionality only:

Given a Ripple address, return a human-readable identifier, if at all possible.

For this, it will attempt to consult a number of different sources:

1. A manually maintained, internal map.

   This should ideally be avoided, but might be useful for well-known
   addresses that still are not identifyable by other means.

2. A registered ripple name.

   For this, query the server at id.ripple.com.

3. Use the domain claimed by the account.

   The ripple.txt file on the domain is reverse-checked to make sure it
   advertises the Ripple address.

4. Look for a custom [x-name] key in the domain's ripple.txt that may
   optionally be used instead of the domain name itself.

The sources are given preference in this order:  local mapping, x-name key,
nickname, domain name.


Usage
-----

You may use the instance that is running at [id.wasipaid.com](id.wasipaid.com).

For example:

   [http://id.wasipaid.com/r3THXKcb5KnJbD5M74kRdMfpoMY1ik8dQ5]()

You may specify a timeout to ensure a quick response:

   [http://id.wasipaid.com/r3THXKcb5KnJbD5M74kRdMfpoMY1ik8dQ5?timeout=0.4]()

If a timeout is used, you will get the best response available at the time. Using
this approach, I recommend that you do not cache the values you receive locally,
if possible - the next time you need the resolve the address, a better response
may be available.
