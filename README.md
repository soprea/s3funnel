# s3funnel

This project is a clone of <https://github.com/shazow>'s s3funnel from google code: <http://code.google.com/p/s3funnel/>. s3funnel is a multithreaded command line tool for Amazon's Simple Storage Service (S3).

- Written in Python, easy_install the package to install as an egg.
- Supports multithreaded operations for large volumes. Put, get, or delete many items concurrently, using a fixed-size pool of threads.
- Built on workerpool for multithreading and boto for access to the Amazon S3 API.
- Unix-friendly input and output. Pipe things in, out, and all around.

## Usage
    $ s3funnel --help
    Usage: s3funnel BUCKET OPERATION [OPTIONS] [FILE]...

    s3funnel is a multithreaded tool for performing operations on Amazon's S3.

    Key Operations:
        DELETE Delete key from the bucket
        GET    Get key from the bucket
        PUT    Put file into the bucket (key is the basename of the path)

    Bucket Operations:
        CREATE Create a new bucket
        LIST   List keys in the bucket. If no bucket is given, all buckets will be listed.


    Options:
        -h, --help            show this help message and exit
        -t N, --threads=N     Number of threads to use [default: 10]
        -T SECONDS, --timeout=SECONDS
                              Socket timeout time, 0 is never [default: 30]
        --secure              Don't use secure (https) connection
        --list-marker=KEY     (`list` only) Start key for list operation
        --list-prefix=STRING  (`list` only) Limit results to a specific prefix
        --list-delimiter=CHAR
                              (`list` only) Treat value as a delimiter for
                              hierarchical listing
        --put-acl=ACL         (`put` only) Set the ACL permission for each file
                              [default: public-read]
        --put-full-path       (`put` only) Use the full given path as the key name,
                              instead of just the basename
        --put-only-new        (`put` only) Only PUT keys which don't already exist
                              in the bucket with the same md5 digest
        --put-header=HEADERS  (`put` only) Add the specified header to the request
        --add-prefix=ADD_PREFIX
                              (`put` and `copy` only) Add specified prefix to keys
                              in destination bucket
        --del-prefix=DEL_PREFIX
                              (`put` and `copy` only) Delete specified prefix from
                              keys in destination bucket
        --source-bucket=SOURCE_BUCKET
                              (`copy` only) Source bucket for files
        --no-ignore-s3fs-dirs
                              (`get` only) Don't ignore s3fs directory objects
        -d, --dry-run         Only show what should be uploaded or downloaded but
                              don't actually do it. May still perform S3 requests to
                              get bucket listings and other information though (only
                              for file transfer commands)
        -i FILE, --input=FILE
                              Read one file per line from a FILE manifest
        -v, --verbose         Enable verbose output. Use twice to enable debug
                              output
        --version             Output version information and exit

## Examples
Note: Appending the -v flag will print useful progress information to stderr. Great for learning the tool.

### List existing buckets
    $ s3funnel list
    mybukkit
### Put files in a bucket
    $ touch 1 2 3
    $ s3funnel mybukkit put 1 2 3
### Put files in a bucket with a prefix
    $ touch 4 5 6
    $ s3funnel s3://mybukkit/myprefix put 4 5 6
### List files in a bucket with a prefix
    $ s3funnel s3://mybukkit/myprefix list
    myprefix/4
    myprefix/5
    myprefix/6
### Copy files from a bucket
    $ rm 1 2 3
    $ s3funnel mybukkit get 1 2 3 --threads=2
    $ ls -1
    1
    2
    3
### Copy files from another bucket
    $ s3funnel mybukkit_copy create
    $ s3funnel mybukkit list | s3funnel mybukkit_copy copy --source-bucket mybukkit --threads=2
### Empty a bucket
    $ s3funnel mybukkit list | s3funnel mybukkit delete
    $ s3funnel mybukkit_copy list | s3funnel mybukkit_copy delete --threads=2