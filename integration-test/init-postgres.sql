SELECT 'CREATE DATABASE skaha'
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'skaha')\gexec

\c skaha;

CREATE TABLE Users
(
    uid      INTEGER GENERATED BY DEFAULT AS IDENTITY NOT NULL,
    username VARCHAR(255),
    CONSTRAINT pk_users PRIMARY KEY (uid)
);

CREATE TABLE Groups
(
    gid       INTEGER GENERATED BY DEFAULT AS IDENTITY NOT NULL,
    groupname VARCHAR(255),
    CONSTRAINT pk_groups PRIMARY KEY (gid)
);

CREATE TABLE Users_groups
(
    Users_uid  INTEGER NOT NULL,
    groups_gid INTEGER NOT NULL
);

ALTER TABLE Users_groups ADD CONSTRAINT fk_usegro_on_group FOREIGN KEY (groups_gid) REFERENCES Groups (gid);
ALTER TABLE Users_groups ADD CONSTRAINT fk_usegro_on_user FOREIGN KEY (Users_uid) REFERENCES Users (uid);

create sequence users_uid_seq1 start with 100000;
create sequence groups_gid_seq1 start with 1000000;
